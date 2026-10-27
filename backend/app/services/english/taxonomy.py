"""Multi-dimensional taxonomy for English learning material.

One course can only carry one ``category`` and one ``difficulty``, which is why
everything in a mixed library collapsed to "其他 / 混合". Materials are actually
described along independent axes, so this module derives several at once:

==================  ==========================================================
dimension           values
==================  ==========================================================
``level``           CEFR ``A1 A2 B1 B2 C1`` — **measured**, not guessed
``topics``          up to 4 of business/daily/travel/food/… (multi-label)
``skills``          listening/speaking/pronunciation/vocabulary/grammar/exam
``format``          dialogue/interview/monologue/lecture/news/story/song
``difficulty``      beginner/intermediate/advanced (legacy, derived from level)
==================  ==========================================================

``level`` is the interesting one. Guessing it from a folder name is hopeless —
"American vs British English" could be anything. Since every lesson has a
transcript, three objective signals are available and they correlate well with
CEFR:

* **sentence length** — A1 learners get short clauses, C1 gets long ones;
* **rare-word ratio** — share of tokens outside everyday English;
* **speech rate** — words per second, the single best listening-difficulty cue.

All of it is computed offline from text we already parsed, so scanning stays
free and deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.english import vocabulary as vocab

LEVELS: tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1")

#: Bumped whenever the scoring changes. The value is stored inside
#: ``lessons.features`` so an incremental scan can tell a row produced by an
#: older algorithm from a current one and re-measure it — otherwise tuning the
#: thresholds would silently leave the whole library on the old scale.
TAXONOMY_VERSION = 2

#: CEFR → the coarse label the old UI used.
LEVEL_TO_DIFFICULTY = {
    "A1": "beginner",
    "A2": "beginner",
    "B1": "intermediate",
    "B2": "advanced",
    "C1": "advanced",
}

#: Band edges — a value below an edge maps to that band index.
#:
#: These are calibrated against a real 397-lesson learner-podcast library
#: rather than textbook assumptions. After sentence normalization the actual
#: distribution is ``sent_len`` 3.6-13.4 (median 6.7), ``rare`` 0.05-0.26
#: (median 0.09) and ``wps`` 1.4-2.8 (median 2.2); with textbook-style edges
#: (~8/11/15/20 words per sentence) 74% of the library collapsed into A2, which
#: made the filter useless. These edges spread it 43% / 48% / 6% across
#: A2 / B1 / B2, which matches what the material actually is.
_SENT_LEN_BANDS = (6.0, 8.0, 10.5, 13.0)
_RARE_BANDS = (0.05, 0.08, 0.11, 0.15)
_WPS_BANDS = (1.70, 2.05, 2.40, 2.75)
#: Not every signal is equally telling; rarity tracks vocabulary load best,
#: and speech rate is the strongest cue for listening difficulty.
_WEIGHTS = (0.35, 0.40, 0.25)

#: Dialogue needs different weights. Measured on the real library, at the same
#: vocabulary difficulty dialogues run 1-3 words per sentence shorter than
#: monologues (rare=mid: 6.96 vs 7.87, rare=high: 7.87 vs 10.74), so scoring
#: them with the monologue weights labels every conversation one band easier
#: than it is. A two-person exchange is short *by nature* — what actually makes
#: it hard is the vocabulary, so that weight moves to rarity.
_WEIGHTS_DIALOGUE = (0.15, 0.60, 0.25)

#: turn density (turns / sentences) above which text counts as a dialogue
_DIALOGUE_DENSITY = 0.15

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]+")


def _band(value: float, edges: tuple[float, ...]) -> int:
    for index, edge in enumerate(edges):
        if value < edge:
            return index
    return len(edges)


@dataclass
class Features:
    """Measurable difficulty signals for one text body."""

    words: int = 0
    sentences: int = 0
    tokens: int = 0
    rare: int = 0
    duration: int = 0
    turns: int = 0
    text: str = ""

    @property
    def sent_len(self) -> float:
        return self.words / self.sentences if self.sentences else 0.0

    @property
    def rare_ratio(self) -> float:
        return self.rare / self.tokens if self.tokens else 0.0

    @property
    def wps(self) -> float:
        return self.words / self.duration if self.duration else 0.0

    @property
    def dialogue_ratio(self) -> float:
        return self.turns / self.sentences if self.sentences else 0.0

    @property
    def is_dialogue(self) -> bool:
        return self.dialogue_ratio >= _DIALOGUE_DENSITY

    def as_dict(self) -> dict[str, float]:
        return {
            "words": self.words,
            "sentences": self.sentences,
            "sent_len": round(self.sent_len, 2),
            "rare_ratio": round(self.rare_ratio, 4),
            "wps": round(self.wps, 2),
            "turns": self.turns,
            "dialogue": self.is_dialogue,
            "v": TAXONOMY_VERSION,
        }


def measure(text: str, *, duration: int = 0, turns: int = 0) -> Features:
    """Extract the difficulty signals from one lesson/course transcript."""
    body = text or ""
    sentences = [s for s in _SENTENCE_SPLIT.split(body) if s.strip()]
    words = _WORD.findall(body)
    tokens = [w.lower() for w in words]
    rare = sum(1 for w in tokens if not vocab.known_form(w))
    return Features(
        words=len(words),
        sentences=len(sentences) or (1 if body.strip() else 0),
        tokens=len(tokens),
        rare=rare,
        duration=max(0, int(duration or 0)),
        turns=max(0, int(turns or 0)),
        text=body,
    )


def level_index(features: Features) -> int:
    """Weighted 0..4 difficulty index (0 = A1, 4 = C1)."""
    if features.words == 0:
        return 2  # no transcript — assume the middle instead of lying
    sent = _band(features.sent_len or 0.0, _SENT_LEN_BANDS)
    rare = _band(features.rare_ratio, _RARE_BANDS)
    rate = _band(features.wps or 0.0, _WPS_BANDS) if features.wps else sent
    weights = _WEIGHTS_DIALOGUE if features.is_dialogue else _WEIGHTS
    score = weights[0] * sent + weights[1] * rare + weights[2] * rate
    return max(0, min(len(LEVELS) - 1, round(score)))


def level_for(features: Features) -> str:
    return LEVELS[level_index(features)]


def difficulty_for(level: str) -> str:
    return LEVEL_TO_DIFFICULTY.get(level, "intermediate")


# ── topics ───────────────────────────────────────────────────────────────


#: topic → keywords (English + Chinese). Matched against title, channel,
#: description and the course's most frequent content words.
TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "商务": ("business", "workplace", "office", "meeting", "career", "negotiat",
             "contract", "client", "colleague", "manager", "interview skills",
             "商务", "职场", "办公", "会议", "谈判", "简历"),
    "日常": ("daily", "everyday", "routine", "morning", "habit", "life", "chore",
             "house", "neighbor", "weekend", "日常", "生活", "习惯"),
    "旅行": ("travel", "trip", "airport", "hotel", "vacation", "holiday", "tour",
             "flight", "passport", "suitcase", "旅行", "旅游", "机场", "酒店"),
    "饮食": ("food", "cook", "recipe", "restaurant", "cafe", "coffee", "breakfast",
             "lunch", "dinner", "meal", "menu", "barista", "grocery", "饮食", "美食",
             "做饭", "餐厅", "咖啡"),
    "健康": ("health", "healthy", "fitness", "exercise", "sleep", "stress", "diet",
             "doctor", "workout", "mental", "健康", "健身", "睡眠", "减压"),
    "科技": ("technology", "tech", "computer", "internet", "software", "ai",
             "phone", "app", "digital", "online", "科技", "电脑", "互联网", "手机"),
    "教育": ("school", "student", "teacher", "study", "learn", "lesson", "course",
             "university", "class", "教育", "学习", "学校", "课程"),
    "新闻": ("news", "headline", "report", "breaking", "politics", "新闻", "时事"),
    "文化": ("culture", "tradition", "festival", "history", "custom", "slang",
             "idiom", "accent", "british", "american", "文化", "传统", "节日",
             "俚语", "习语", "口音"),
    "娱乐": ("movie", "film", "tv", "series", "celebrity", "game", "fun", "comedy",
             "娱乐", "电影", "电视", "游戏"),
    "音乐": ("song", "music", "lyric", "sing", "band", "concert", "音乐", "歌曲"),
    "语法": ("grammar", "tense", "verb", "sentence", "structure", "phrasal",
             "语法", "时态", "动词", "句型"),
    "词汇": ("vocabulary", "word", "phrase", "idiom", "collocation", "expression",
             "词汇", "单词", "短语", "表达"),
    "发音": ("pronunciation", "pronounce", "accent", "sound", "phonetic",
             "shadowing", "发音", "音标", "跟读"),
    "考试": ("ielts", "toefl", "cet", "exam", "test", "score", "雅思", "托福",
             "四级", "六级", "考试"),
    "社交": ("friend", "social", "party", "relationship", "family", "date",
             "社交", "朋友", "聚会", "家庭"),
    "科学": ("science", "scientific", "physics", "biology", "space", "climate",
             "research", "cognition", "cognitive", "experiment", "theory",
             "analysis", "data", "statistic", "algorithm",
             "科学", "物理", "生物", "宇宙", "研究"),
    "运动": ("sport", "football", "basketball", "run", "gym", "swim", "运动", "足球"),
}

# ── skills ───────────────────────────────────────────────────────────────

SKILL_RULES: dict[str, tuple[str, ...]] = {
    "听力": ("listen", "listening", "ear", "听力", "听懂"),
    "口语": ("speak", "speaking", "conversation", "talk", "dialog", "口语", "对话"),
    "发音": ("pronunciation", "pronounce", "accent", "phonetic", "shadowing",
             "发音", "音标", "跟读"),
    "词汇": ("vocabulary", "word", "phrase", "idiom", "slang", "collocation",
             "expression", "词汇", "单词", "短语", "俚语"),
    "语法": ("grammar", "tense", "verb form", "语法", "时态"),
    "阅读": ("read", "reading", "article", "阅读"),
    "写作": ("write", "writing", "essay", "email", "写作"),
    "考试": ("ielts", "toefl", "cet", "exam", "考试", "雅思", "托福"),
}

# ── format ───────────────────────────────────────────────────────────────

FORMAT_LABEL = {
    "dialogue": "对话",
    "interview": "访谈",
    "monologue": "独白",
    "lecture": "讲座",
    "news": "新闻",
    "story": "故事",
    "song": "歌曲",
}

_FORMAT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("interview", ("interview", "q&a", "questions and answers", "访谈", "问答")),
    ("news", ("news", "headline", "新闻", "播报")),
    ("song", ("song", "lyric", "sing", "music video", "歌曲", "音乐")),
    ("story", ("story", "tale", "故事")),
    ("lecture", ("ted", "lecture", "talk", "course", "lesson", "讲座", "精讲", "课程")),
)


_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}


#: Inflections a bare keyword should still match ("cook" → "cooking").
_INFLECTIONS = r"(?:s|es|ed|d|ing)?"


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Boundary-aware pattern for ASCII keywords; plain substring otherwise.

    Bare ``in`` matching produced real false positives — the 科技 keyword "ai"
    matched inside "d**ai**ly", so a beginner course was tagged technology. But
    requiring a boundary on both sides is too strict in the other direction:
    "cook" then stops matching "cooking". So ASCII keywords get a leading
    boundary (which kills the "daily" case) plus optional inflections.
    """
    pattern = _BOUNDARY_CACHE.get(keyword)
    if pattern is None:
        escaped = re.escape(keyword)
        if keyword.isascii():
            pattern = re.compile(rf"(?<![a-z0-9]){escaped}{_INFLECTIONS}(?![a-z0-9])")
        else:
            # Chinese has no word boundaries — substring is the correct rule.
            pattern = re.compile(escaped)
        _BOUNDARY_CACHE[keyword] = pattern
    return pattern


def _hit(haystack: str, keyword: str) -> bool:
    return _keyword_pattern(keyword).search(haystack) is not None


def _score_keywords(haystack: str, rules: dict[str, tuple[str, ...]]) -> dict[str, int]:
    scores: dict[str, int] = {}
    for label, keywords in rules.items():
        hits = sum(1 for keyword in keywords if _hit(haystack, keyword))
        if hits:
            scores[label] = hits
    return scores


def topics_for(
    *,
    titles: list[str],
    description: str | None = None,
    content_words: list[str] | None = None,
    limit: int = 4,
) -> list[str]:
    """Multi-label topic detection.

    Title and description are weighted above raw content words: a video called
    "Ordering Coffee in English" is about 饮食 even if the transcript happens to
    say "contract" twice.
    """
    head = " ".join(t for t in titles if t).lower()
    desc = (description or "").lower()
    scores = _score_keywords(head, TOPIC_RULES)
    for label, hits in _score_keywords(desc, TOPIC_RULES).items():
        scores[label] = scores.get(label, 0) + hits

    if content_words:
        body = " ".join(content_words[:200])
        for label, keywords in TOPIC_RULES.items():
            bonus = sum(1 for k in keywords if _hit(body, k))
            if bonus:
                scores[label] = scores.get(label, 0) + min(bonus, 4) * 0.5

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    picked = [label for label, _ in ranked[:limit]]
    # An untagged course would be invisible under every topic filter, so give
    # it the same «其他» bucket the legacy category column uses.
    return picked or ["其他"]


#: What a media form trains, used when the title says nothing explicit.
SKILL_BY_FORMAT: dict[str, tuple[str, ...]] = {
    "dialogue": ("口语", "听力"),
    "interview": ("口语", "听力"),
    "lecture": ("听力",),
    "news": ("听力",),
    "story": ("听力", "阅读"),
    "song": ("听力", "发音"),
    "monologue": ("听力",),
}

#: A topic implies a skill.
SKILL_BY_TOPIC: dict[str, str] = {
    "语法": "语法",
    "词汇": "词汇",
    "发音": "发音",
    "考试": "考试",
}


def skills_for(
    *,
    titles: list[str],
    description: str | None = None,
    media_format: str | None = None,
    topics: list[str] | None = None,
    limit: int = 3,
) -> list[str]:
    """Explicit title keywords first, then what the form/topic implies.

    A podcast about grammar trains 听力 whatever its title says, so an empty
    skill list was misleading — and an empty filter is useless.
    """
    head = " ".join(t for t in titles if t).lower()
    desc = (description or "").lower()
    scores = _score_keywords(head, SKILL_RULES)
    for label, hits in _score_keywords(desc, SKILL_RULES).items():
        scores[label] = scores.get(label, 0) + hits

    ranked = [label for label, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]
    for topic in topics or []:
        implied = SKILL_BY_TOPIC.get(topic)
        if implied and implied not in ranked:
            ranked.append(implied)
    if media_format:
        for implied in SKILL_BY_FORMAT.get(media_format, ()):
            if implied not in ranked:
                ranked.append(implied)
    return ranked[:limit]


def format_from_title(titles: list[str]) -> str | None:
    """``None`` when the titles carry no format keyword at all."""
    head = " ".join(t for t in titles if t).lower()
    for label, keywords in _FORMAT_RULES:
        if any(_hit(head, keyword) for keyword in keywords):
            return label
    return None


def format_for(
    *,
    titles: list[str],
    turns: int = 0,
    duration: int = 0,
    sentence_count: int = 0,
) -> str:
    """Media form, from the title first and the transcript second."""
    titled = format_from_title(titles)
    if titled:
        return titled
    # Two speakers keep swapping → it is a conversation, whatever the title says.
    # Four turns is enough: a short interview only carries a handful of them.
    if turns >= 4 and sentence_count:
        density = turns / max(1, sentence_count)
        if density >= 0.20:
            return "dialogue"
    return "monologue"


# ── aggregate ────────────────────────────────────────────────────────────


@dataclass
class Taxonomy:
    level: str = "B1"
    topics: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    format: str = "monologue"
    difficulty: str = "intermediate"
    features: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "difficulty": self.difficulty,
            "topics": self.topics,
            "skills": self.skills,
            "format": self.format,
            "features": self.features,
        }


def classify_lesson(
    *,
    title: str,
    course_titles: list[str] | None = None,
    text: str = "",
    duration: int = 0,
    turns: int = 0,
    content_words: list[str] | None = None,
) -> Taxonomy:
    """Classify ONE lesson — the file, not the course.

    A course is only an aggregate; individual episodes differ a lot (an intro
    episode is easier than the debate one). Everything here is measured from
    this lesson's own transcript, with the course titles used as *context* for
    topic detection: a single title like "001 - Getting Started" carries no
    topic words, so falling back to the course name is the honest choice.
    """
    features = measure(text, duration=duration, turns=turns)
    level = level_for(features)

    lesson_topics = topics_for(titles=[title], content_words=content_words)
    if lesson_topics == ["其他"] and course_titles:
        # Nothing in the episode title; borrow the course's subject matter.
        lesson_topics = topics_for(
            titles=[title] + list(course_titles), content_words=content_words
        )

    media_format = (
        format_from_title([title])
        or format_for(
            titles=[title],
            turns=turns,
            duration=duration,
            sentence_count=features.sentences,
        )
    )

    return Taxonomy(
        level=level,
        difficulty=difficulty_for(level),
        topics=lesson_topics,
        skills=skills_for(
            titles=[title] + list(course_titles or []),
            media_format=media_format,
            topics=lesson_topics,
        ),
        format=media_format,
        features=features.as_dict(),
    )


def aggregate_level(levels: list[str]) -> str:
    """Median lesson level — a course must not be pulled by one outlier."""
    indices = sorted(
        LEVELS.index(lv) for lv in levels if isinstance(lv, str) and lv in LEVELS
    )
    if not indices:
        return "B1"
    return LEVELS[indices[len(indices) // 2]]


def aggregate_labels(
    groups: list[list[str] | None], *, limit: int = 4
) -> list[str]:
    """Most common labels across the lessons, keeping a stable order."""
    counts: dict[str, int] = {}
    for group in groups:
        for label in group or []:
            counts[str(label)] = counts.get(str(label), 0) + 1
    if not counts:
        return []
    return [
        label
        for label, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    ]


def aggregate_format(formats: list[str | None]) -> str:
    """Majority format, preferring the more specific label on a tie."""
    counts: dict[str, int] = {}
    for value in formats:
        if value:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return "monologue"
    best = max(counts.values())
    for preferred in ("dialogue", "interview", "lecture", "story", "song", "news", "monologue"):
        if counts.get(preferred) == best:
            return preferred
    return next(iter(counts))


__all__ = [
    "FORMAT_LABEL",
    "LEVELS",
    "TAXONOMY_VERSION",
    "LEVEL_TO_DIFFICULTY",
    "SKILL_RULES",
    "TOPIC_RULES",
    "Features",
    "Taxonomy",
    "aggregate_format",
    "aggregate_labels",
    "aggregate_level",
    "classify_lesson",
    "difficulty_for",
    "format_for",
    "format_from_title",
    "level_for",
    "level_index",
    "measure",
    "skills_for",
    "topics_for",
]
