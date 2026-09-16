import re
from collections import Counter

from .resources import (
    CONTRACTIONS,
    REVERSE_CONTRACTIONS,
    STOPWORDS,
)

NEGATION_RE = re.compile(r"\b(?:not|never|no|without|nothing|nor|cannot|n't)\b", re.IGNORECASE)
RHETORICAL_NEGATION_RE = re.compile(r"\bnot (?:just|only|merely)\b", re.IGNORECASE)
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?%?")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
SENT_BOUNDARY_RE = re.compile(r"(?:^|[.!?]\s+|\n)")

_CONTRACTION_PARTS = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in REVERSE_CONTRACTIONS) + r")\b", re.IGNORECASE
)
_CAPITALIZED = re.compile(r"\b[A-Z][a-zA-Z]*\b|\b[A-Z]{2,}\b")


def _expand_contractions(text: str) -> str:
    def sub(m):
        full = REVERSE_CONTRACTIONS.get(m.group(0).lower())
        return full if full else m.group(0)

    return _CONTRACTION_PARTS.sub(sub, text)


def _numbers(text):
    return Counter(NUMBER_RE.findall(text))


def _proper_nouns(text):
    nouns = set()
    for m in _CAPITALIZED.finditer(text):
        w = m.group(0)
        if w == "I":
            continue
        if w.isupper() and len(w) > 1:
            nouns.add(w.lower())
            continue
        start = m.start()
        before = text[max(0, start - 2) : start]
        if start == 0 or SENT_BOUNDARY_RE.search(before + " ") or "\n" in before:
            continue
        nouns.add(w.lower())
    return nouns


def _content_words(text):
    out = []
    for w in WORD_RE.findall(_expand_contractions(text)):
        lw = w.lower()
        if lw.endswith("'s"):
            lw = lw[:-2]
        if len(lw) > 2 and lw not in STOPWORDS:
            out.append(lw)
    return out


def _sentence_claim_alignment(original: str, rewritten: str, droppable: set[str] | None = None) -> list[str]:
    from .resources import split_sentences

    o_sents = [s for s in split_sentences(original) if len(_content_words(s)) >= 4]
    n_sents = [s for s in split_sentences(rewritten) if len(_content_words(s)) >= 4]
    if len(o_sents) < 3 or not n_sents:
        return []
    n_sets = [set(_content_words(n)) for n in n_sents]
    problems: list[str] = []
    drop = droppable or set()
    for o in o_sents:
        ocw = {w for w in _content_words(o) if w not in drop}
        if len(ocw) < 3:
            continue
        best = max(
            (len(ocw & ns) / max(1, len(ocw)) for ns in n_sets),
            default=0.0,
        )
        if best < 0.5:
            problems.append(f"sentence claims dropped: {o[:60]!r}")
    return problems


def semantic_check(original: str, rewritten: str, allowlist: set[str], droppable: set[str] | None = None) -> list[str]:
    problems: list[str] = []
    if _numbers(original) != _numbers(rewritten):
        problems.append("numbers or quantities changed")

    o = RHETORICAL_NEGATION_RE.sub(" ", _expand_contractions(original).lower())
    n = RHETORICAL_NEGATION_RE.sub(" ", _expand_contractions(rewritten).lower())
    if Counter(NEGATION_RE.findall(o)) != Counter(NEGATION_RE.findall(n)):
        problems.append("negations added or removed")

    dropped_nouns = _proper_nouns(original) - _proper_nouns(rewritten)
    if dropped_nouns:
        problems.append(f"proper nouns dropped: {sorted(dropped_nouns)[:5]}")

    drop = droppable or set()
    oc = Counter(_content_words(original))
    nc = Counter(_content_words(rewritten))
    missing = {w: c for w, c in (oc - nc).items() if c > 0 and w not in drop}
    if missing:
        problems.append(f"claims dropped: {sorted(missing)[:8]}")

    invented = {
        w for w in (nc - oc)
        if w not in allowlist and w not in STOPWORDS
    }
    if invented:
        problems.append(f"unsupported content added: {sorted(invented)[:8]}")

    problems += _sentence_claim_alignment(original, rewritten, droppable)
    return problems
