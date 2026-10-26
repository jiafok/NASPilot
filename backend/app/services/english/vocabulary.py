"""Vocabulary extraction — finds words a learner probably does not know yet.

The previous implementation returned the most *frequent* content words, which
is nearly the opposite of useful: a list that opens with ``price ×2`` and
``schedule ×2`` teaches nothing. What makes a word worth studying is that it is

(a) not already everyday English,
(b) specific to this material,
(c) used often enough to actually be noticed.

Candidates are therefore filtered and ranked by three cheap, offline signals:

* ``common_words.COMMON_WORDS`` — A2/B1 vocabulary, filtered out;
* library document frequency — a word in most lessons of the library is common
  *here*, whatever a dictionary says about it;
* capitalisation pattern — tokens always capitalised mid-sentence are names.

Extraction stays local: no network, no tokens, and scanning 400 lessons is
still about a second of CPU.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from app.services.english.common_words import COMMON_WORDS

#: Content-word tokenizer: letters plus internal apostrophes/hyphens.
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]{1,}")
_CAP_TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]{1,}")
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

#: Grammar words + everyday content words — both count as "already known".
_KNOWN = STOPWORDS | COMMON_WORDS

#: Irregular forms that no suffix rule can reach (``seen`` → ``see``).
IRREGULAR: dict[str, str] = {
    "was": "be", "were": "be", "been": "be", "am": "be", "is": "be", "are": "be",
    "saw": "see", "seen": "see", "seeing": "see",
    "went": "go", "gone": "go", "goes": "go",
    "took": "take", "taken": "take", "gave": "give", "given": "give",
    "made": "make", "did": "do", "done": "do", "said": "say", "told": "tell",
    "thought": "think", "brought": "bring", "bought": "buy", "found": "find",
    "got": "get", "gotten": "get", "left": "leave", "felt": "feel", "kept": "keep",
    "knew": "know", "known": "know", "ran": "run", "came": "come",
    "became": "become", "began": "begin", "begun": "begin",
    "wrote": "write", "written": "write", "spoke": "speak", "spoken": "speak",
    "ate": "eat", "eaten": "eat", "drank": "drink", "drunk": "drink",
    "slept": "sleep", "woke": "wake", "woken": "wake", "wore": "wear", "worn": "wear",
    "chose": "choose", "chosen": "choose", "threw": "throw", "thrown": "throw",
    "flew": "fly", "flown": "fly", "grew": "grow", "grown": "grow",
    "drew": "draw", "drawn": "draw", "broke": "break", "broken": "break",
    "stole": "steal", "stolen": "steal", "hid": "hide", "hidden": "hide",
    "bit": "bite", "bitten": "bite", "fell": "fall", "fallen": "fall",
    "rose": "rise", "risen": "rise", "sent": "send", "spent": "spend",
    "built": "build", "lent": "lend", "meant": "mean", "met": "meet",
    "paid": "pay", "sold": "sell", "stood": "stand", "understood": "understand",
    "won": "win", "lost": "lose", "shot": "shoot", "taught": "teach",
    "caught": "catch", "fought": "fight", "sought": "seek", "heard": "hear",
    "held": "hold", "hung": "hang", "lay": "lie", "laid": "lay",
    "led": "lead", "fed": "feed", "fled": "flee", "dug": "dig",
    "sang": "sing", "sung": "sing", "swam": "swim", "swum": "swim",
    "sat": "sit", "set": "set", "put": "put", "cut": "cut", "let": "let",
    "shut": "shut", "hit": "hit", "read": "read", "ran": "run",
    "spat": "spit", "swept": "sweep", "wept": "weep", "dealt": "deal",
    "snuck": "sneak", "stuck": "stick", "sprang": "spring",
}

#: Words that usually start a useful collocation as the first element.
_WEAK_LEADERS = frozenset({"the", "a", "an", "to", "of", "in", "on", "and", "or"})

#: Longest first — otherwise "speakers" loses only the "s".
_SUFFIXES = (
    "ations", "ation", "ingly", "ings", "edly", "ing", "est", "ies", "ed", "ly", "es", "er", "s",
)
#: Endings after which the base may need an "e" restored ("making" -> "make").
_E_RESTORE = ("ing", "ings", "edly", "ed", "es", "er", "est")


def is_known(word: str) -> bool:
    """True when the word is everyday English as-is."""
    return word.lower() in _KNOWN


def _lemma_once(word: str) -> str:
    w = word.lower()
    for suffix in _SUFFIXES:
        if not w.endswith(suffix) or len(w) - len(suffix) < 3:
            continue
        base = w[: -len(suffix)]
        candidates = [base]
        if suffix == "ies":
            candidates = [base + "y", base]
        elif suffix in _E_RESTORE:
            candidates.append(base + "e")
            # "stopped" -> "stopp" -> "stop"; never for s/l/f/z ("dressed")
            if len(base) > 3 and base[-1] == base[-2] and base[-1] not in "aeiou" + "slfz":
                candidates.append(base[:-1])
        for candidate in candidates:
            if is_known(candidate):
                return candidate
        return candidates[0]
    return w


def lemma(word: str, *, passes: int = 2) -> str:
    """Crude English stemmer - matches ``negotiating`` -> ``negotiate``.

    Two passes so a doubly-suffixed form still lands on a known word:
    ``speakers`` -> ``speaker`` -> ``speak``.
    """
    current = word.lower()
    for _ in range(passes):
        nxt = _lemma_once(current)
        if nxt == current:
            break
        current = nxt
    return current


def known_form(word: str) -> bool:
    """True when the word or any of its stems is everyday English."""
    w = word.lower()
    if is_known(w):
        return True
    if is_known(IRREGULAR.get(w, "")):
        return True
    current = w
    for _ in range(2):
        current = _lemma_once(current)
        if is_known(current) or is_known(IRREGULAR.get(current, "")):
            return True
    return False


def _form_key(word: str) -> str:
    """Group key so ``translate`` / ``translates`` / ``translating`` collapse.

    The stem is compared without a trailing ``e`` and without doubled
    consonants, which is what makes the three surface forms above land on the
    same key (``translat``) without needing a real stemmer.
    """
    w = word.lower()
    if w in IRREGULAR:
        return IRREGULAR[w]
    key = _lemma_once(w)
    if len(key) > 4 and key.endswith("e"):
        key = key[:-1]
    if len(key) > 4 and key[-1] == key[-2] and key[-1] not in "aeiou":
        key = key[:-1]
    return key


def word_difficulty(word: str, frequency: int, *, rarity: float = 0.0) -> str:
    """Rough CEFR-ish band: short & frequent → beginner, long & rare → advanced."""
    length = len(word.replace("'", "").replace("-", ""))
    if length >= 10 or rarity >= 0.6:
        return "advanced"
    if length >= 7 or rarity >= 0.3 or frequency >= 4:
        return "intermediate"
    return "beginner"


@dataclass
class VocabularyResult:
    """Candidate words/phrases plus the scores behind them."""

    words: Counter[str] = field(default_factory=Counter)
    phrases: Counter[str] = field(default_factory=Counter)
    scores: dict[str, float] = field(default_factory=dict)
    rarities: dict[str, float] = field(default_factory=dict)

    def merged(self, *, limit: int, min_phrase_freq: int = 2) -> list[dict[str, object]]:
        """Words + phrases ranked by usefulness, ready for DB upsert."""
        items: list[dict[str, object]] = []
        for word, freq in self.words.items():
            items.append(
                {
                    "word": word,
                    "kind": "word",
                    "frequency": freq,
                    "difficulty": word_difficulty(
                        word, freq, rarity=self.rarities.get(word, 0.0)
                    ),
                    "score": self.scores.get(word, float(freq)),
                }
            )
        for phrase, freq in self.phrases.items():
            if freq < min_phrase_freq:
                continue
            items.append(
                {
                    "word": phrase,
                    "kind": "phrase",
                    "frequency": freq,
                    "difficulty": "advanced",
                    "score": self.scores.get(phrase, float(freq)) * 0.8,
                }
            )
        items.sort(key=lambda x: (-float(x["score"]), str(x["word"])))
        return items[:limit]


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


def library_document_frequency(texts: list[str]) -> tuple[Counter[str], int]:
    """How many documents contain each word — the corpus for rarity scoring."""
    df: Counter[str] = Counter()
    kept = [t for t in texts if t]
    for text in kept:
        for word in set(tokenize(text)):
            df[word] += 1
    return df, len(kept)


def _proper_nouns(text: str) -> set[str]:
    """Tokens capitalised mid-sentence — i.e. names, not vocabulary."""
    caps: Counter[str] = Counter()
    mid: Counter[str] = Counter()
    for match in _CAP_TOKEN.finditer(text):
        token = match.group(0)
        if not token[0].isupper():
            continue
        low = token.lower()
        caps[low] += 1
        before = text[: match.start()].rstrip()
        if before and before[-1] not in '.!?"“”\n':
            mid[low] += 1
    return {word for word, count in mid.items() if count >= max(2, caps[word] * 0.5)}


def extract_new_words(
    texts: list[str],
    *,
    library_df: Counter[str] | None = None,
    library_size: int = 0,
    limit: int = 300,
    min_freq: int = 2,
    max_df_ratio: float = 0.10,
    min_length: int = 4,
) -> VocabularyResult:
    """Extract the words of this material that are worth studying.

    ``library_df`` / ``library_size`` describe the whole library and are what
    make the result material-specific: a word showing up in a third of all
    lessons is common English *for this user*, so it is filtered out.
    """
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return VocabularyResult()

    freq: Counter[str] = Counter(tokenize(blob))
    names = _proper_nouns(blob)
    result = VocabularyResult()
    scoring: list[tuple[float, str, int, float]] = []

    for word, count in freq.items():
        if count < min_freq or len(word) < min_length:
            continue
        if "'" in word or "-" in word:  # contractions / hyphens are noisy
            continue
        if known_form(word):
            continue
        if word in names:
            continue

        df = int(library_df.get(word, 0)) if library_df is not None else 0
        if library_size and df / library_size > max_df_ratio:
            continue

        rarity = 0.0
        if library_size:
            rarity = 1.0 - math.log1p(df) / math.log1p(library_size)
        # frequent here + rare in the library + longer words win
        score = count * (1.0 + rarity * 3.0) * (1.0 + 0.10 * (len(word) - min_length))
        scoring.append((score, word, count, rarity))

    # Collapse inflected forms: "translate" / "translating" / "translated" are
    # one word to a learner, so they become a single entry with summed counts.
    grouped: dict[str, list[float | int | str]] = {}
    for score, word, count, rarity in scoring:
        key = _form_key(word)
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = [score, word, count, rarity]
            continue
        entry[0] = float(entry[0]) + score
        entry[2] = int(entry[2]) + count
        # keep the shortest surface form as the display word
        if len(word) < len(str(entry[1])):
            entry[1] = word
        entry[3] = max(float(entry[3]), rarity)

    ranking = sorted(
        (v for v in grouped.values()),
        key=lambda e: (-float(e[0]), str(e[1])),
    )
    for score, word, count, rarity in ranking[: max(limit * 3, limit)]:
        word = str(word)
        result.words[word] = int(count)
        result.scores[word] = float(score)
        result.rarities[word] = float(rarity)

    # Collocations: keep only pairs where a part is itself worth learning, so
    # the list never fills up with "of the"-style noise.
    keep = set(result.words)
    for sentence in sentences(blob):
        seq = [
            t
            for t in tokenize(sentence)
            if t not in STOPWORDS and t not in _WEAK_LEADERS and len(t) > 2
        ]
        for i in range(len(seq) - 1):
            if seq[i] in keep or seq[i + 1] in keep:
                result.phrases[f"{seq[i]} {seq[i + 1]}"] += 1

    return result


def extract(text: str, *, min_word_freq: int = 2) -> VocabularyResult:
    return extract_new_words([text], min_freq=min_word_freq)


def extract_many(texts: list[str], *, min_word_freq: int = 2) -> VocabularyResult:
    return extract_new_words(texts, min_freq=min_word_freq)


__all__ = [
    "STOPWORDS",
    "VocabularyResult",
    "extract",
    "extract_many",
    "extract_new_words",
    "is_known",
    "known_form",
    "lemma",
    "library_document_frequency",
    "sentences",
    "tokenize",
    "word_difficulty",
]
