"""Single-word dictionary lookup for the learner's tap-a-word card.

Why this exists: ``EnglishVocabulary`` is a *per-course* list of 300 interesting
words produced by the scanner. The player lets the learner tap any word in the
subtitle, and most of those are not in that list — so there was no data source
for the word card at all.

Design:

* The AI is asked for a tiny, strictly-shaped JSON document. Anything unparsable
  is rejected rather than shown, because a wrong definition is worse than none.
* Answers are cached by normalised surface form in ``word_lookups``, so the
  same word is paid for once no matter how many lessons contain it.
* A builtin micro-dictionary covers the handful of words that appear in almost
  every lesson, so the card still works when the AI is not configured.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import begin_write
from app.models import EnglishVocabulary, EnglishWordLookup
from app.services import ai_service

logger = logging.getLogger("naspilot.english.dictionary")

#: Words that are worth answering without any network round-trip. Kept tiny on
#: purpose: this is a fallback for an unconfigured AI, not a dictionary.
BUILTIN: dict[str, dict[str, Any]] = {
    "about": {"phonetic": "/əˈbaʊt/", "senses": [{"pos": "prep.", "meaning": "关于；大约"}]},
    "after": {"phonetic": "/ˈɑːftə/", "senses": [{"pos": "prep.", "meaning": "在……之后"}]},
    "again": {"phonetic": "/əˈɡen/", "senses": [{"pos": "adv.", "meaning": "再一次"}]},
    "always": {"phonetic": "/ˈɔːlweɪz/", "senses": [{"pos": "adv.", "meaning": "总是；一直"}]},
    "because": {"phonetic": "/bɪˈkɒz/", "senses": [{"pos": "conj.", "meaning": "因为"}]},
    "before": {"phonetic": "/bɪˈfɔː/", "senses": [{"pos": "prep.", "meaning": "在……之前"}]},
    "could": {"phonetic": "/kʊd/", "senses": [{"pos": "modal", "meaning": "能够（can 的过去式）；可以"}]},
    "every": {"phonetic": "/ˈevri/", "senses": [{"pos": "det.", "meaning": "每一个"}]},
    "maybe": {"phonetic": "/ˈmeɪbi/", "senses": [{"pos": "adv.", "meaning": "也许；可能"}]},
    "never": {"phonetic": "/ˈnevə/", "senses": [{"pos": "adv.", "meaning": "从不"}]},
    "really": {"phonetic": "/ˈrɪəli/", "senses": [{"pos": "adv.", "meaning": "真的；确实"}]},
    "should": {"phonetic": "/ʃʊd/", "senses": [{"pos": "modal", "meaning": "应该"}]},
    "something": {"phonetic": "/ˈsʌmθɪŋ/", "senses": [{"pos": "pron.", "meaning": "某事；某物"}]},
    "sometimes": {"phonetic": "/ˈsʌmtaɪmz/", "senses": [{"pos": "adv.", "meaning": "有时"}]},
    "thank": {"phonetic": "/θæŋk/", "senses": [{"pos": "v.", "meaning": "感谢"}]},
    "there": {"phonetic": "/ðeə/", "senses": [{"pos": "adv.", "meaning": "那里；（there be）有"}]},
    "thing": {"phonetic": "/θɪŋ/", "senses": [{"pos": "n.", "meaning": "事情；东西"}]},
    "think": {"phonetic": "/θɪŋk/", "senses": [{"pos": "v.", "meaning": "想；认为"}]},
    "through": {"phonetic": "/θruː/", "senses": [{"pos": "prep.", "meaning": "穿过；通过"}]},
    "together": {"phonetic": "/təˈɡeðə/", "senses": [{"pos": "adv.", "meaning": "一起"}]},
    "usually": {"phonetic": "/ˈjuːʒuəli/", "senses": [{"pos": "adv.", "meaning": "通常"}]},
    "want": {"phonetic": "/wɒnt/", "senses": [{"pos": "v.", "meaning": "想要"}]},
    "would": {"phonetic": "/wʊd/", "senses": [{"pos": "modal", "meaning": "将会；愿意"}]},
}

#: Keep the request short — a word, not a sentence, and no markup.
_PROMPT = """请为英语学习者解释单词「{word}」{context}。

严格只输出 JSON，不要任何解释、不要 markdown 代码块：
{{"word":"{word}","phonetic":"/音标/","senses":[{{"pos":"词性缩写","meaning":"中文释义"}}],"example":"一句包含该词的英文例句及中文翻译"}}

要求：
- phonetic 用英式或美式音标，用斜杠包裹；不确定就给空字符串
- senses 最多 3 条，按该词在本语境下最可能的释义排序
- example 不超过 30 个英文单词，后面跟中文翻译
"""

_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'’\-]{0,62}$")


def normalize(word: str) -> str:
    """Cache key: lower-cased, trimmed, typographic apostrophes unified."""
    return (word or "").strip().lower().replace("’", "'").strip(" .,!?;:\"()[]")


#: Inflection suffixes, longest first so "studies" -> "study" wins over "studie".
_INFLECTIONS = ("ies", "es", "s", "ied", "ed", "ing", "ly", "er", "est")


def _stem_variants(word: str) -> list[str]:
    """Base forms worth trying when the surface form has no entry.

    The learner taps whatever the subtitle says ("habits", "started"), while
    the dictionary stores lemmas. Without this every inflected word missed both
    the builtin list and the course vocabulary and fell through to the AI.
    """
    out: list[str] = []
    for suffix in _INFLECTIONS:
        if not word.endswith(suffix) or len(word) - len(suffix) < 3:
            continue
        base = word[: -len(suffix)]
        out.append(base)
        if suffix == "ies":
            out.append(base + "y")
        # "running" -> "run" (the consonant was doubled)
        if len(base) > 2 and base[-1] == base[-2]:
            out.append(base[:-1])
        # "making" -> "make" (silent e dropped before -ing)
        if suffix in {"ing", "ed", "er", "est"} and not base.endswith("e"):
            out.append(base + "e")
    return [candidate for candidate in dict.fromkeys(out) if candidate != word]


def is_lookupable(word: str) -> bool:
    return bool(_WORD_RE.match(normalize(word)))


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model response.

    Models like to wrap answers in ```json fences or add a sentence before it,
    so a plain ``json.loads`` is not enough.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    fenced = re.search(r"\{.*\}", raw, re.S)
    if not fenced:
        return None
    try:
        data = json.loads(fenced.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _sanitize(data: dict[str, Any], word: str, source: str) -> dict[str, Any] | None:
    senses: list[dict[str, str]] = []
    raw_senses = data.get("senses")
    if isinstance(raw_senses, list):
        for item in raw_senses[:3]:
            if not isinstance(item, dict):
                continue
            meaning = str(item.get("meaning") or "").strip()
            if not meaning:
                continue
            senses.append({"pos": str(item.get("pos") or "").strip()[:16], "meaning": meaning[:200]})
    if not senses:
        # A single string fallback keeps an otherwise usable answer alive.
        meaning = str(data.get("meaning") or "").strip()
        if not meaning:
            return None
        senses = [{"pos": str(data.get("pos") or "").strip()[:16], "meaning": meaning[:200]}]

    return {
        "word": word,
        "phonetic": str(data.get("phonetic") or "").strip()[:64],
        "senses": senses,
        "example": str(data.get("example") or "").strip()[:400],
        "source": source,
    }


async def _from_course_vocabulary(db: AsyncSession, word: str) -> dict[str, Any] | None:
    """Reuse a meaning the scanner already produced for a course word."""
    row = (
        await db.execute(
            select(EnglishVocabulary)
            .where(func.lower(EnglishVocabulary.word) == word, EnglishVocabulary.meaning.isnot(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None or not row.meaning:
        return None
    return {
        "word": word,
        "phonetic": "",
        "senses": [{"pos": row.kind if row.kind in {"n", "v", "adj", "adv"} else "", "meaning": row.meaning}],
        "example": row.example or "",
        "source": "vocabulary",
    }


async def lookup(
    db: AsyncSession,
    word: str,
    *,
    sentence: str | None = None,
    use_ai: bool = True,
) -> dict[str, Any] | None:
    """Resolve one word, using the cache, the scanner's list, then the AI."""
    key = normalize(word)
    if not key or not is_lookupable(key):
        return None

    cached = (
        await db.execute(select(EnglishWordLookup).where(EnglishWordLookup.word == key))
    ).scalar_one_or_none()
    if cached is not None and isinstance(cached.payload, dict):
        payload = dict(cached.payload)
        payload["word"] = key
        payload["cached"] = True
        # Hit counting is best-effort: it must never fail a lookup.
        try:
            await db.execute(
                update(EnglishWordLookup)
                .where(EnglishWordLookup.id == cached.id)
                .values(hits=EnglishWordLookup.hits + 1)
            )
            await db.commit()
        except Exception:  # pragma: no cover - statistics only
            await db.rollback()
        return payload

    if key in BUILTIN:
        entry = dict(BUILTIN[key])
        entry.update({"word": key, "example": "", "source": "builtin", "cached": False})
        await _store(db, key, entry, source="builtin")
        return entry

    from_vocab = await _from_course_vocabulary(db, key)
    if from_vocab is not None:
        await _store(db, key, from_vocab, source="vocabulary")
        from_vocab["cached"] = False
        return from_vocab

    # Inflected forms: serve the lemma's cached/builtin/vocabulary entry so the
    # card answers instantly and consistently. The AI path still gets the
    # original surface form and the sentence as context.
    for variant in _stem_variants(key):
        if variant in BUILTIN:
            entry = dict(BUILTIN[variant])
            entry.update({"word": key, "example": "", "source": "builtin", "cached": False})
            await _store(db, key, entry, source="builtin")
            return entry
        lemma_cached = (
            await db.execute(select(EnglishWordLookup).where(EnglishWordLookup.word == variant))
        ).scalar_one_or_none()
        if lemma_cached is not None and isinstance(lemma_cached.payload, dict):
            entry = dict(lemma_cached.payload)
            entry.update({"word": key, "cached": False, "lemma": variant})
            await _store(db, key, entry, source=str(entry.get("source") or "cache"))
            return entry
        lemma_vocab = await _from_course_vocabulary(db, variant)
        if lemma_vocab is not None:
            lemma_vocab["word"] = key
            lemma_vocab["lemma"] = variant
            await _store(db, key, lemma_vocab, source="vocabulary")
            lemma_vocab["cached"] = False
            return lemma_vocab

    if not use_ai:
        return None

    context = f"（出现在句子中：{sentence.strip()[:200]}）" if sentence else ""
    try:
        answer = await ai_service.ask(_PROMPT.format(word=key, context=context), db=db)
    except ai_service.AIUnavailable:
        # Propagate: "AI 未配置" is actionable, "查不到该词" is not.
        raise
    except Exception:
        logger.warning("Word lookup failed for %r", key, exc_info=True)
        return None

    parsed = _extract_json(answer)
    if parsed is None:
        logger.warning("Word lookup returned unparsable JSON for %r", key)
        return None
    entry = _sanitize(parsed, key, "ai")
    if entry is None:
        return None
    await _store(db, key, entry, source="ai")
    entry["cached"] = False
    return entry


async def _store(db: AsyncSession, key: str, payload: dict[str, Any], *, source: str) -> None:
    """Persist one entry. Failure is non-fatal — the answer is still returned."""
    try:
        await begin_write(db)
        db.add(EnglishWordLookup(word=key, payload=payload, source=source))
        await db.commit()
    except Exception:  # pragma: no cover - duplicate / lock contention
        await db.rollback()
        logger.debug("Could not cache word %r", key, exc_info=True)


__all__ = ["BUILTIN", "is_lookupable", "lookup", "normalize"]
