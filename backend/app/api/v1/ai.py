"""AI Assistant endpoint — diagnose and suggest fixes using LLM."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.services import ai_service
from app.services.system_service import get_system_stats

router = APIRouter(prefix="/ai", tags=["ai"])


class AIRequest(BaseModel):
    question: str
    include_system_stats: bool = True


class AIResponse(BaseModel):
    answer: str
    context_used: list[str] = []


def _build_context(stats: dict) -> str:
    lines = [
        f"CPU 使用率: {stats.get('cpu_percent', '?')}%",
        f"内存使用率: {stats.get('memory_percent', '?')}% "
        f"({stats.get('memory_used', 0) // 1024 // 1024 // 1024:.1f}GB / "
        f"{stats.get('memory_total', 0) // 1024 // 1024 // 1024:.1f}GB)",
        f"磁盘使用率: {stats.get('disk_percent', '?')}% "
        f"({stats.get('disk_used', 0) // 1024 // 1024 // 1024:.1f}GB / "
        f"{stats.get('disk_total', 0) // 1024 // 1024 // 1024:.1f}GB)",
    ]
    return "\n".join(lines)


@router.post("/ask", response_model=AIResponse, summary="Ask AI assistant")
async def ask_ai(
    body: AIRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Ops assistant — same AI client the English tutor uses."""
    context_used: list[str] = []
    system_parts = [
        "你是 NASPilot 的 AI 运维助手，专门帮助 NAS/HomeLab 用户诊断系统问题、优化配置和处理自动化任务。",
        "请用中文回答，给出简洁实用的建议。",
    ]

    if body.include_system_stats:
        stats = get_system_stats()
        system_parts.append(f"\n当前系统状态:\n{_build_context(stats)}")
        context_used.append("system_stats")

    try:
        answer = await ai_service.ask(
            body.question,
            system="\n".join(system_parts),
            db=db,
        )
    except ai_service.AIUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI API 调用失败: {exc}") from exc

    return AIResponse(answer=answer, context_used=context_used)
