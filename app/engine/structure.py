import difflib
import re

from .resources import STOPWORDS, WORD_RE, split_sentences

MARKER_TOKENS = {
    "also", "plus", "and", "but", "so", "still", "yet", "now", "then", "however",
    "moreover", "furthermore", "additionally", "consequently", "therefore", "thus",
    "hence", "meanwhile", "besides", "ultimately", "overall", "notably", "importantly",
    "frankly", "honestly", "look", "granted", "first", "second", "third", "finally",
    "lately", "these", "right", "in", "that", "to", "it",
}

SUBORDINATORS = {"which", "that", "because", "since", "while", "although", "though", "whereas", "if", "when", "after", "before", "unless", "until"}


def _content_trigrams(text: str) -> set[tuple[str, ...]]:
    ws = [w.lower() for w in WORD_RE.findall(text)]
    content = [w for w in ws if w not in STOPWORDS]
    return {tuple(content[i : i + 3]) for i in range(max(0, len(content) - 2))}


def _openers(text: str) -> list[str]:
    out = []
    for s in split_sentences(text):
        ws = WORD_RE.findall(s)
        if ws:
            out.append(ws[0].lower().strip(",.;:!?\"'"))
    return out


def _lens(text: str) -> list[int]:
    return [len(WORD_RE.findall(s)) for s in split_sentences(text) if WORD_RE.findall(s)]


def _subordinator_rate(text: str) -> float:
    ws = [w.lower() for w in WORD_RE.findall(text)]
    return sum(1 for w in ws if w in SUBORDINATORS) / max(1, len(ws))


def structural_distance(original: str, rewritten: str) -> float:
    o_lens, n_lens = _lens(original), _lens(rewritten)
    lens_sim = difflib.SequenceMatcher(None, o_lens, n_lens, autojunk=False).ratio()

    o_open, n_open = _openers(original), _openers(rewritten)
    o_set, n_set = {o for o in o_open if o not in MARKER_TOKENS}, {o for o in n_open if o not in MARKER_TOKENS}
    opener_overlap = len(o_set & n_set) / max(1, len(o_set | n_set))

    o_tri, n_tri = _content_trigrams(original), _content_trigrams(rewritten)
    tri_overlap = len(o_tri & n_tri) / max(1, len(o_tri))

    sub_delta = abs(_subordinator_rate(original) - _subordinator_rate(rewritten))

    distance = (
        0.35 * (1 - lens_sim)
        + 0.25 * (1 - opener_overlap)
        + 0.30 * (1 - tri_overlap)
        + 0.10 * min(1.0, sub_delta * 4)
    )
    return round(max(0.0, min(1.0, distance)), 3)


def lexical_diversity(text: str) -> dict:
    ws = [w.lower() for w in WORD_RE.findall(text)]
    n = len(ws)
    if n < 30:
        return {"ttr": None, "distinct_trigram_ratio": None}
    ttr = len(set(ws)) / n
    tris = [tuple(ws[i : i + 3]) for i in range(max(0, n - 2))]
    tri_ratio = len(set(tris)) / max(1, len(tris))
    return {"ttr": round(ttr, 3), "distinct_trigram_ratio": round(tri_ratio, 3)}


def lexical_diversity_regression(original: str, rewritten: str) -> list[str]:
    problems: list[str] = []
    o, r = lexical_diversity(original), lexical_diversity(rewritten)
    if o["ttr"] is not None and r["ttr"] is not None and r["ttr"] < o["ttr"] * 0.92:
        problems.append(f"lexical diversity dropped: {o['ttr']} -> {r['ttr']}")
    if (
        o["distinct_trigram_ratio"] is not None
        and r["distinct_trigram_ratio"] is not None
        and r["distinct_trigram_ratio"] < o["distinct_trigram_ratio"] * 0.9
    ):
        problems.append(f"trigram diversity dropped: {o['distinct_trigram_ratio']} -> {r['distinct_trigram_ratio']}")
    o_open, n_open = _openers(original), _openers(rewritten)
    if n_open:
        from collections import Counter

        c = Counter(n_open)
        top, cnt = c.most_common(1)[0]
        if cnt / len(n_open) > 0.4 and len(n_open) >= 4:
            problems.append(f"openers too repetitive: {top} x{cnt}")
    return problems
