import re

from .resources import WORD_RE
from .transforms import FINITE_VERB_RE

DOUBLED_WORD_RE = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)
DOUBLED_FN_RE = re.compile(r"\b(and|but|so|or|the|a|an|to|of|in|is|was|that|for)\s+\1\b", re.IGNORECASE)
PUNCT_ERRORS_RE = re.compile(r",\s*\.\s|\.\s*,|\s{2,}[,;.:]")
EM_DASH_RE = re.compile(r"[—–]")
LOWERCASE_START_RE = re.compile(r"(?:^|[.!?]\s+|\n)\s*[a-z]")
MULTI_MODAL_RE = re.compile(r"\b(?:will|can|may|might|must|should|would|could)\s+(?:will|can|may|might|must|should|would|could)\b", re.IGNORECASE)

BANNED_COLLOCATION_RES = [
    re.compile(r"\bmajor to\b", re.IGNORECASE),
    re.compile(r"\bput together (?:decisions?|choices?|plans?)\b", re.IGNORECASE),
    re.compile(r"\bgig displacement\b", re.IGNORECASE),
    re.compile(r"\bin a flash (?:chang|grow|mov|evolv|improv|spread|ris|shar)\w*", re.IGNORECASE),
    re.compile(r"\bquick as anything (?:chang|grow|mov|improv)\w*", re.IGNORECASE),
    re.compile(r"\bplenty of (?:a |an |the )\b", re.IGNORECASE),
    re.compile(r"\bvery very\b", re.IGNORECASE),
    re.compile(r"\bmore better\b", re.IGNORECASE),
    re.compile(r"\bmost best\b", re.IGNORECASE),
    re.compile(r"\b(?:is|are|was|were)\s+(?:is|are|was|were)\b", re.IGNORECASE),
    re.compile(r"\bhowever,? (?:but|yet|though)\b", re.IGNORECASE),
    re.compile(r"\b(?:and|but|so)\s*(?:and|but|so)\b", re.IGNORECASE),
    re.compile(r"\bstill, yet\b", re.IGNORECASE),
    re.compile(r"\bdiscuss about\b", re.IGNORECASE),
    re.compile(r"\bcountless of\b", re.IGNORECASE),
    re.compile(r"\bmajorly\b", re.IGNORECASE),
    re.compile(r"\bcause due to\b", re.IGNORECASE),
]

UNBALANCED_PARENS_RE = re.compile(r"\((?:[^()]*\([^()]*\)[^()]*\()|[^()]*\)(?:[^()]*\()[^()]*\(")


def _sentences(text):
    from .resources import split_sentences

    return [s.strip() for s in split_sentences(text) if s.strip()]


def grammar_check(text: str, allow_short_fragments: bool = True) -> list[str]:
    problems: list[str] = []
    em_count = len(EM_DASH_RE.findall(text))
    n_words = max(1, len(WORD_RE.findall(text)))
    if em_count * 1000 / n_words > 3:
        problems.append("em dash rate too high")
    for m in DOUBLED_WORD_RE.finditer(text):
        problems.append(f"doubled word: {m.group(0)!r}")
    for pat in BANNED_COLLOCATION_RES:
        m = pat.search(text)
        if m:
            problems.append(f"collocation: {m.group(0)!r}")
    for m in MULTI_MODAL_RE.finditer(text):
        problems.append(f"stacked modals: {m.group(0)!r}")
    if text.count("(") != text.count(")"):
        problems.append("unbalanced parentheses")
    if text.count('"') % 2 == 1:
        problems.append("unbalanced quotes")
    for m in PUNCT_ERRORS_RE.finditer(text):
        problems.append(f"punctuation: {m.group(0)!r}")
    for s in _sentences(text):
        n_words = len(WORD_RE.findall(s))
        if n_words <= 10 or ":" in s or "|" in s:
            continue
        clauses = re.split(r",\s*", s)
        if not any(FINITE_VERB_RE.search(c) for c in clauses):
            problems.append(f"verbless sentence: {s[:60]!r}")
    return problems
