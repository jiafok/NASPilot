"""English Learning Center — Learning Open API + media streaming.

Mounted twice:
* `/api/v1/english/*` — versioned, used by the web UI
* `/api/english/*`    — unversioned alias, the Flutter client contract

Streaming routes live in their own routers so the numeric `/api/media/{id}`
alias can be registered *after* the Media Download Center routes (literal paths
win, so `/api/media/subscriptions` keeps working).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser
from app.core.security import decode_access_token
from app.models import EnglishCourse, EnglishLesson, EnglishTranscript, User
from app.services import ai_service
from app.services.english import dictionary, query, scanner, stream

logger = logging.getLogger("naspilot.english.api")

router = APIRouter(prefix="/english", tags=["english"])


async def _stream_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    token: Annotated[
        str | None,
        Query(description="Bearer token — <video>/<audio>/<img> 无法发送请求头时使用"),
    ] = None,
) -> User:
    """Auth for streaming routes: Bearer header first, then ``?token=``.

    HTML media elements cannot attach custom headers, so the web player passes
    the same JWT as a query parameter. Flutter keeps using the header.
    """
    raw = (token or "").strip()
    if not raw:
        header = request.headers.get("authorization") or ""
        if header.lower().startswith("bearer "):
            raw = header[7:].strip()

    payload = decode_access_token(raw) if raw else None
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="未认证")

    user = await db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    return user


StreamUser = Annotated[User, Depends(_stream_user)]

#: Clean, unambiguous streaming paths (`/api/stream/...`).
stream_router = APIRouter(tags=["english-stream"])

#: Client-contract aliases (`/api/media/{id}`, `/api/subtitle/{id}`).
#: Registered after the Media Download Center so literal routes win.
alias_router = APIRouter(tags=["english-stream"])


# ══════════════════════════════════════════════════════════════════════
#  Schemas
# ══════════════════════════════════════════════════════════════════════


class ProgressIn(BaseModel):
    lesson_id: int
    position: float = Field(0, ge=0)
    listen_seconds: int = Field(0, ge=0)
    completed: bool | None = None


class BookmarkIn(BaseModel):
    lesson_id: int
    position: int = Field(..., ge=0)
    at_seconds: float = Field(0, ge=0)
    note: str | None = None
    text: str | None = None
    remove: bool = False


class FavoriteIn(BaseModel):
    course_id: int
    favorite: bool = True


class WordIn(BaseModel):
    word: str = Field(..., min_length=1, max_length=64)
    #: The sentence the word was tapped in — improves the definition picked.
    sentence: str | None = Field(None, max_length=400)
    lesson_id: int | None = None


class ChatIn(BaseModel):
    lesson_id: int
    question: str = Field(..., min_length=1, max_length=2000)


class ExplainIn(BaseModel):
    lesson_id: int
    position: int = Field(..., ge=0)


class SummaryIn(BaseModel):
    lesson_id: int | None = None
    course_id: int | None = None
    language: str = "zh"


def _sse(event: str, data: dict[str, Any]) -> str:
    """Format one server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ══════════════════════════════════════════════════════════════════════
#  Read endpoints
# ══════════════════════════════════════════════════════════════════════


@router.get("/home", summary="首页聚合（EnglishFlow 首页）")
async def english_home(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await query.home_payload(db, user)


@router.get("/courses", summary="课程列表")
async def english_courses(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str | None = Query(None, description="标题/频道/描述搜索"),
    channel: str | None = None,
    category: str | None = None,
    difficulty: str | None = None,
    level: str | None = Query(None, description="CEFR: A1 | A2 | B1 | B2 | C1"),
    topic: str | None = Query(None, description="话题，如 商务 / 日常 / 饮食"),
    skill: str | None = Query(None, description="技能，如 听力 / 词汇 / 语法"),
    format: str | None = Query(None, description="形式：dialogue | interview | …"),
    favorite: bool = False,
    sort: str = Query("recent", description="recent | title | lessons"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return await query.list_courses(
        db,
        user.id,
        q=q,
        channel=channel,
        category=category,
        difficulty=difficulty,
        level=level,
        topic=topic,
        skill=skill,
        format=format,
        favorite=favorite or None,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get("/filters", summary="筛选器可选项")
async def english_filters(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await query.course_filters(db)


@router.get("/stats", summary="学习统计（连续天数 / 每日曲线 / 热力图 / 总量）")
async def english_stats(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(90, ge=7, le=730, description="热力图覆盖的天数"),
):
    return await query.stats_payload(db, user.id, days=days)


@router.post("/word", summary="单词查询（词典卡）")
async def english_word(
    body: WordIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Resolve one word for the tap-a-word card.

    Cached by normalised form, so repeat taps cost nothing. Returns 404 only
    when the AI is genuinely unable to answer (unconfigured, or an answer that
    could not be parsed) — never a fabricated definition.
    """
    try:
        entry = await dictionary.lookup(db, body.word, sentence=body.sentence)
    except ai_service.AIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="暂时无法查到该词，请稍后重试")
    return entry


@router.get("/lessons", summary="课时列表（按单个文件的难度/话题筛选）")
async def english_lessons(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str | None = Query(None, description="标题 / 课程搜索"),
    course_id: int | None = None,
    level: str | None = Query(None, description="CEFR: A1 | A2 | B1 | B2 | C1"),
    topic: str | None = Query(None, description="话题，如 商务 / 日常 / 饮食"),
    skill: str | None = Query(None, description="技能，如 听力 / 词汇 / 语法"),
    format: str | None = Query(None, description="形式：dialogue | interview | …"),
    sort: str = Query("recent", description="recent | title | duration"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return await query.list_lessons(
        db,
        user.id,
        q=q,
        course_id=course_id,
        level=level,
        topic=topic,
        skill=skill,
        format=format,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get("/course/{course_id}", summary="课程详情（含课时列表）")
async def english_course(
    course_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    payload = await query.course_detail(db, course_id, user.id)
    if payload is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    return payload


@router.get("/lesson/{lesson_id}", summary="课时详情（播放地址 / 字幕 / 生词）")
async def english_lesson(
    lesson_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    payload = await query.lesson_detail(db, lesson_id, user.id)
    if payload is None:
        raise HTTPException(status_code=404, detail="课时不存在")
    return payload


@router.get("/transcript/{lesson_id}", summary="字幕句子（毫秒时间戳）")
async def english_transcript(
    lesson_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    payload = await query.transcript_payload(db, lesson_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="课时不存在")
    return payload


@router.get("/vocabulary/{course_id}", summary="课程生词表")
async def english_vocabulary(
    course_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(300, ge=1, le=1000),
):
    payload = await query.vocabulary_payload(db, course_id, limit=limit)
    if payload is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    return payload


@router.get("/search", summary="字幕全文搜索（MVP: LIKE）")
async def english_search(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
):
    like = f"%{q.strip()}%"
    rows = (
        await db.execute(
            select(EnglishTranscript, EnglishLesson, EnglishCourse)
            .join(EnglishLesson, EnglishLesson.id == EnglishTranscript.lesson_id)
            .join(EnglishCourse, EnglishCourse.id == EnglishLesson.course_id)
            .where(EnglishTranscript.search_text.ilike(like))
            .limit(limit)
        )
    ).all()
    items: list[dict[str, Any]] = []
    needle = q.strip().lower()
    for transcript, lesson, course in rows:
        snippet = ""
        for seg in transcript.segments or []:
            if needle in str(seg.get("text", "")).lower():
                snippet = str(seg.get("text", ""))
                break
        items.append(
            {
                "lesson_id": lesson.id,
                "lesson_title": lesson.title,
                "course_id": course.id,
                "course_title": course.title,
                "snippet": snippet,
            }
        )
    return {"query": q, "items": items, "total": len(items)}


# ══════════════════════════════════════════════════════════════════════
#  Write endpoints
# ══════════════════════════════════════════════════════════════════════


@router.post("/progress", summary="上报学习进度")
async def english_progress(
    body: ProgressIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    payload = await query.upsert_progress(
        db,
        user.id,
        lesson_id=body.lesson_id,
        position=body.position,
        listen_seconds=body.listen_seconds,
        completed=body.completed,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="课时不存在")
    return payload


@router.post("/bookmark", summary="句级书签（新增/更新/删除）")
async def english_bookmark(
    body: BookmarkIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    payload = await query.upsert_bookmark(
        db,
        user.id,
        lesson_id=body.lesson_id,
        position=body.position,
        at_seconds=body.at_seconds,
        note=body.note,
        text=body.text,
        remove=body.remove,
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="课时不存在")
    return payload


@router.post("/favorite", summary="收藏 / 取消收藏课程")
async def english_favorite(
    body: FavoriteIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    payload = await query.set_favorite(
        db, user.id, course_id=body.course_id, favorite=body.favorite
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    return payload


# ══════════════════════════════════════════════════════════════════════
#  AI tutor (reuses the shared AI service)
# ══════════════════════════════════════════════════════════════════════

_TUTOR_SYSTEM = (
    "你是一位耐心的英语老师，面向中文母语的学习者。"
    "回答使用中文解释 + 必要的英文例句。简洁、准确、可操作。"
    "如果提供了字幕上下文，请只依据该上下文分析。"
)


def _ai_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ai_service.AIUnavailable):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=502, detail=f"AI 调用失败: {exc}")


@router.post("/ai/chat", summary="AI 老师问答（结合字幕上下文）")
async def english_ai_chat(
    body: ChatIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    lesson, context = await query.lesson_context(db, body.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="课时不存在")

    parts = [f"课程：《{lesson.title}》"]
    if context:
        parts.append(f"字幕内容（节选）：\n{context}")
    parts.append(f"学生问题：{body.question}")

    # The model call takes seconds; holding the write lock for it would block
    # every other write (progress reports, logins) for the whole duration.
    await db.commit()

    try:
        answer = await ai_service.ask("\n\n".join(parts), system=_TUTOR_SYSTEM, db=db)
    except Exception as exc:
        raise _ai_error(exc) from exc

    return {
        "answer": answer,
        "context_used": ["transcript"] if context else [],
        "lesson_id": lesson.id,
    }


@router.post("/ai/chat/stream", summary="AI 老师问答（SSE 流式）")
async def english_ai_chat_stream(
    body: ChatIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Same answer as ``/ai/chat``, delivered as server-sent events.

    Non-streaming left the client blank for the whole generation. Event shapes:

        event: delta   data: {"text": "..."}      # append to the answer
        event: done    data: {"lesson_id": 12}    # generation finished
        event: error   data: {"detail": "..."}    # AI unavailable / failed
    """
    lesson, context = await query.lesson_context(db, body.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="课时不存在")

    parts = [f"课程：《{lesson.title}》"]
    if context:
        parts.append(f"字幕内容（节选）：\n{context}")
    parts.append(f"学生问题：{body.question}")
    prompt = "\n\n".join(parts)
    lesson_id = lesson.id

    # Resolve AI settings while the session is still usable: the generator runs
    # long after this coroutine returns, and the DB dependency is only torn down
    # once the response finishes, so it must not hold a transaction while we
    # stream.
    try:
        ai_config = await ai_service.resolve_config(db)
    except Exception:  # pragma: no cover - config problems surface as AIUnavailable
        ai_config = None
    await db.rollback()

    async def event_stream():
        try:
            async for fragment in ai_service.stream_chat(
                [{"role": "user", "content": prompt}],
                system=_TUTOR_SYSTEM,
                config=ai_config,
            ):
                yield _sse("delta", {"text": fragment})
            yield _sse("done", {"lesson_id": lesson_id})
        except ai_service.AIUnavailable as exc:
            yield _sse("error", {"detail": str(exc)})
        except Exception as exc:  # pragma: no cover - provider failure
            logger.warning("AI stream failed", exc_info=True)
            yield _sse("error", {"detail": f"AI 调用失败：{exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Tell nginx & friends not to buffer, or the "stream" arrives at once.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/ai/explain", summary="逐句讲解")
async def english_ai_explain(
    body: ExplainIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    transcript = (
        await db.execute(
            select(EnglishTranscript).where(EnglishTranscript.lesson_id == body.lesson_id)
        )
    ).scalar_one_or_none()
    lesson = await db.get(EnglishLesson, body.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="课时不存在")
    if transcript is None:
        raise HTTPException(status_code=400, detail="该课时没有字幕，无法逐句讲解")

    segment = query.segment_at(transcript.segments or [], body.position)
    if segment is None:
        raise HTTPException(status_code=404, detail="句子不存在")

    sentence = str(segment.get("text", ""))
    prompt = (
        f"请讲解这句话：\n{sentence}\n\n"
        "要求：1) 中文翻译 2) 关键语法点 3) 地道用法或搭配 4) 一句类似例句。"
    )
    # The model call takes seconds; holding the write lock for it would block
    # every other write (progress reports, logins) for the whole duration.
    await db.commit()

    try:
        answer = await ai_service.ask(prompt, system=_TUTOR_SYSTEM, db=db)
    except Exception as exc:
        raise _ai_error(exc) from exc

    return {
        "answer": answer,
        "sentence": sentence,
        "position": body.position,
        "at_seconds": round(int(segment.get("start", 0)) / 1000, 2),
    }


@router.post("/ai/summary", summary="课程 / 课时总结")
async def english_ai_summary(
    body: SummaryIn,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    language = (body.language or "zh").strip().lower()
    lang_text = "中文" if language.startswith("zh") else "English"

    if body.lesson_id is not None:
        lesson, context = await query.lesson_context(db, body.lesson_id)
        if lesson is None:
            raise HTTPException(status_code=404, detail="课时不存在")
        if not context:
            raise HTTPException(status_code=400, detail="该课时没有字幕，无法总结")
        prompt = (
            f"请用{lang_text}总结这节英语课《{lesson.title}》的内容。\n"
            "输出格式：\n1. 一句话主旨\n2. 3-5 个要点\n3. 3 个值得记住的表达\n"
            f"\n字幕：\n{context}"
        )
    elif body.course_id is not None:
        course = await db.get(EnglishCourse, body.course_id)
        if course is None:
            raise HTTPException(status_code=404, detail="课程不存在")
        lessons = (
            await db.execute(
                select(EnglishLesson)
                .where(EnglishLesson.course_id == course.id, EnglishLesson.status == "ready")
                .order_by(EnglishLesson.position.asc())
                .limit(50)
            )
        ).scalars().all()
        titles = "\n".join(f"- {ls.title}" for ls in lessons) or "（暂无课时）"
        prompt = (
            f"请用{lang_text}介绍这门英语课程《{course.title}》"
            f"（频道 {course.channel}，分类 {course.category}，难度 {course.difficulty}）。\n"
            f"课时列表：\n{titles}\n\n"
            "输出：适合谁学、学完能获得什么、建议学习顺序。"
        )
    else:
        raise HTTPException(status_code=400, detail="请提供 lesson_id 或 course_id")

    try:
        answer = await ai_service.ask(prompt, system=_TUTOR_SYSTEM, db=db)
    except Exception as exc:
        raise _ai_error(exc) from exc

    return {"summary": answer, "language": language}


@router.get("/ai/status", summary="AI 是否已配置")
async def english_ai_status(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    cfg = await ai_service.resolve_config(db)
    return {"configured": cfg.configured, "model": cfg.model if cfg.configured else None}


# ══════════════════════════════════════════════════════════════════════
#  Admin / management
# ══════════════════════════════════════════════════════════════════════


@router.get("/directories", summary="媒体库根目录与状态")
async def english_directories(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    stats = await scanner.stats(db)
    return {"root": str(scanner.english_root()), "stats": stats}


@router.post("/admin/scan", summary="扫描媒体库（幂等）")
async def english_scan(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    with_vocabulary: bool = Query(True, description="是否重建生词表"),
):
    report = await scanner.scan(db, with_vocabulary=with_vocabulary)
    return report


@router.get("/admin/stats", summary="媒体库统计")
async def english_stats(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await scanner.stats(db)


@router.post("/admin/vocabulary/{course_id}", summary="重新提取课程生词")
async def english_rebuild_vocabulary(
    course_id: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    course = await db.get(EnglishCourse, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    library_df, library_size = await scanner.library_stats(db)
    count = await scanner.refresh_course_vocabulary(
        db, course, library_df=library_df, library_size=library_size
    )
    await db.commit()
    return {"course_id": course_id, "terms": count}


@router.get("/admin/lessons", summary="课时列表（管理/排查用）")
async def english_admin_lessons(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    course_id: int | None = None,
    include_missing: bool = False,
    limit: int = Query(200, ge=1, le=1000),
):
    stmt = select(EnglishLesson)
    if course_id is not None:
        stmt = stmt.where(EnglishLesson.course_id == course_id)
    if not include_missing:
        stmt = stmt.where(EnglishLesson.status == "ready")
    rows = (
        await db.execute(stmt.order_by(EnglishLesson.id.desc()).limit(limit))
    ).scalars().all()
    return {
        "items": [
            {
                "id": ls.id,
                "course_id": ls.course_id,
                "title": ls.title,
                "media_type": ls.media_type,
                "duration": ls.duration,
                "status": ls.status,
                "has_subtitle": bool(ls.subtitle_path),
                "level": ls.level or "B1",
                "topics": list(ls.topics or []),
                "format": ls.format or "monologue",
                "features": ls.features or {},
                "file_path": ls.file_path,
            }
            for ls in rows
        ],
        "total": len(rows),
    }


# ══════════════════════════════════════════════════════════════════════
#  Streaming
# ══════════════════════════════════════════════════════════════════════


async def _load_lesson(db: AsyncSession, lesson_id: int) -> EnglishLesson:
    lesson = await db.get(EnglishLesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="课时不存在")
    return lesson


def _guarded(path_str: str | None) -> Path:
    """Resolve a DB-stored path and confirm it is inside the library root."""
    if not path_str:
        raise HTTPException(status_code=404, detail="文件未登记")
    path = Path(path_str)
    root = scanner.english_root()
    if not scanner.is_inside_root(path, root):
        logger.warning("Blocked out-of-root media access: %s", path)
        raise HTTPException(status_code=403, detail="路径越界")
    stream.ensure_readable(path)
    return path


@stream_router.get("/stream/media/{lesson_id}", summary="媒体流（支持 Range）")
@alias_router.get("/media/{lesson_id}", summary="媒体流（客户端契约别名）")
async def english_stream_media(
    lesson_id: int,
    request: Request,
    user: StreamUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    lesson = await _load_lesson(db, lesson_id)
    if lesson.status != "ready":
        raise HTTPException(status_code=410, detail="文件已不存在于媒体库")
    path = _guarded(lesson.file_path)
    # End the transaction BEFORE the body starts streaming.
    #
    # FastAPI only tears a yield-dependency down after the response finishes.
    # A paused player keeps its connection open indefinitely, so the session
    # stayed alive with an open transaction - and because every transaction
    # opens with BEGIN IMMEDIATE, that held SQLite's write lock for as long as
    # anyone left a lesson paused, blocking every other write in the process
    # (logins failed after busy_timeout with "database is locked").
    await db.rollback()
    return stream.stream_file(path, request)


@stream_router.get("/stream/subtitle/{lesson_id}", summary="字幕原文")
@alias_router.get("/subtitle/{lesson_id}", summary="字幕原文（客户端契约别名）")
async def english_stream_subtitle(
    lesson_id: int,
    user: StreamUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    lesson = await _load_lesson(db, lesson_id)
    if not lesson.subtitle_path:
        raise HTTPException(status_code=404, detail="该课时没有字幕")
    path = _guarded(lesson.subtitle_path)
    # End the transaction BEFORE the body starts streaming.
    #
    # FastAPI only tears a yield-dependency down after the response finishes.
    # A paused player keeps its connection open indefinitely, so the session
    # stayed alive with an open transaction - and because every transaction
    # opens with BEGIN IMMEDIATE, that held SQLite's write lock for as long as
    # anyone left a lesson paused, blocking every other write in the process
    # (logins failed after busy_timeout with "database is locked").
    await db.rollback()
    return stream.subtitle_text(path)


@stream_router.get("/stream/cover/{course_id}", summary="课程封面")
async def english_stream_cover(
    course_id: int,
    user: StreamUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    course = await db.get(EnglishCourse, course_id)
    if course is None or not course.cover_path:
        raise HTTPException(status_code=404, detail="封面不存在")
    path = _guarded(course.cover_path)
    # End the transaction BEFORE the body starts streaming.
    #
    # FastAPI only tears a yield-dependency down after the response finishes.
    # A paused player keeps its connection open indefinitely, so the session
    # stayed alive with an open transaction - and because every transaction
    # opens with BEGIN IMMEDIATE, that held SQLite's write lock for as long as
    # anyone left a lesson paused, blocking every other write in the process
    # (logins failed after busy_timeout with "database is locked").
    await db.rollback()
    return FileResponse(
        path,
        media_type=stream.guess_mime(path),
        headers={"Cache-Control": "private, max-age=3600"},
    )


@stream_router.get("/stream/lesson-cover/{lesson_id}", summary="课时封面")
async def english_stream_lesson_cover(
    lesson_id: int,
    user: StreamUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    lesson = await _load_lesson(db, lesson_id)
    if not lesson.cover_path:
        raise HTTPException(status_code=404, detail="封面不存在")
    path = _guarded(lesson.cover_path)
    # End the transaction BEFORE the body starts streaming.
    #
    # FastAPI only tears a yield-dependency down after the response finishes.
    # A paused player keeps its connection open indefinitely, so the session
    # stayed alive with an open transaction - and because every transaction
    # opens with BEGIN IMMEDIATE, that held SQLite's write lock for as long as
    # anyone left a lesson paused, blocking every other write in the process
    # (logins failed after busy_timeout with "database is locked").
    await db.rollback()
    return FileResponse(
        path,
        media_type=stream.guess_mime(path),
        headers={"Cache-Control": "private, max-age=3600"},
    )


__all__ = ["alias_router", "router", "stream_router"]
