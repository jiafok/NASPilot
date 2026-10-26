"""Vocabulary extraction (local, no AI).

Pulls candidate words and collocations out of transcript text. AI is only used
later, on demand, to attach a meaning — extraction itself must stay offline so
scanning a whole library never costs tokens.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

#: Content-word tokenizer: letters plus internal apostrophes/hyphens.
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]{1,}")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

#: High-frequency function words — worthless as "new words".
STOPWORDS: frozenset[str] = frozenset(
    """
    a about above after again against all am an and any are aren't as at be because been
    before being below between both but by can can't cannot could couldn't did didn't do
    does doesn't doing don't down during each few for from further had hadn't has hasn't
    have haven't having he he'd he'll he's her here here's hers herself him himself his how
    how's i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most
    mustn't my myself no nor not of off on once only or other ought our ours ourselves out
    over own same shan't she she'd she'll she's should shouldn't so some such than that
    that's the their theirs them themselves then there there's these they they'd they'll
    they're they've this those through to too under until up very was wasn't we we'd we'll
    we're we've were weren't what what's when when's where where's which while who who's
    whom why why's with won't would wouldn't you you'd you'll you're you've your yours
    yourself yourselves also just really quite gonna wanna gotta yeah yes okay oh um uh
    like well now right thing things get got go going went come came make made know knew
    say said says see saw look looks want wanted need needed think thought take took give
    gave tell told put use used find found ask asked work worked seem seemed feel felt try
    tries trying lot lots kind sort maybe actually pretty much many
    """.split()
)

#: Words that usually start a useful collocation as the first element.
_WEAK_LEADERS = frozenset({"the", "a", "an", "to", "of", "in", "on", "and", "or"})


@dataclass
class VocabularyResult:
    words: Counter[str] = field(default_factory=Counter)
    phrases: Counter[str] = field(default_factory=Counter)

    def merged(self, *, limit: int, min_phrase_freq: int = 2) -> list[dict[str, object]]:
        """Words + phrases sorted by frequency, ready for DB upsert."""
        items: list[dict[str, object]] = []
        for word, freq in self.words.most_common():
            items.append(
                {
                    "word": word,
                    "kind": "word",
                    "frequency": freq,
                    "difficulty": word_difficulty(word, freq),
                }
            )
        for phrase, freq in self.phrases.most_common():
            if freq < min_phrase_freq:
                continue
            items.append(
                {
                    "word": phrase,
                    "kind": "phrase",
                    "frequency": freq,
                    "difficulty": "intermediate" if len(phrase.split()) == 2 else "advanced",
                }
            )
        items.sort(key=lambda x: (-int(x["frequency"]), str(x["word"])))
        return items[:limit]


def word_difficulty(word: str, frequency: int) -> str:
    """Rough heuristic: short & frequent → beginner, long & rare → advanced."""
    length = len(word.replace("'", "").replace("-", ""))
    if length <= 5 and frequency >= 3:
        return "beginner"
    if length <= 8:
        return "intermediate"
    return "advanced"


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for match in _TOKEN.finditer(text or ""):
        token = match.group(0).strip("'-").lower()
        if len(token) < 3:
            continue
        out.append(token)
    return out


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def extract(text: str, *, min_word_freq: int = 2) -> VocabularyResult:
    """Extract content words and 2-word collocations from one text blob."""
    tokens = tokenize(text)
    counts = Counter(tokens)
    result = VocabularyResult()

    for word, freq in counts.items():
        if word in STOPWORDS:
            continue
        if freq < min_word_freq:
            continue
        result.words[word] = freq

    # Collocations: consecutive content words inside a sentence.
    for sentence in sentences(text):
        seq = [
            t
            for t in tokenize(sentence)
            if t not in STOPWORDS and t not in _WEAK_LEADERS and len(t) > 2
        ]
        for i in range(len(seq) - 1):
            result.phrases[f"{seq[i]} {seq[i + 1]}"] += 1

    return result


def extract_many(texts: list[str], *, min_word_freq: int = 2) -> VocabularyResult:
    return extract("\n".join(texts), min_word_freq=min_word_freq)


__all__ = [
    "STOPWORDS",
    "VocabularyResult",
    "extract",
    "extract_many",
    "sentences",
    "tokenize",
    "word_difficulty",
]
