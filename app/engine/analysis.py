import re
from collections import Counter

from .resources import (
    AI_PHRASES,
    FUNCTION_WORDS,
    AI_WORDS,
    CONNECTIVE_STARTS,
    CONTRACTIONS,
    EMOJI_RE,
    SENT_SPLIT_RE,
    UNICODE_FIXES,
    word_ranks,
)

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
CONTRACTION_RE = re.compile(
    r"\b\w+['’](?:t|s|re|ve|ll|d|m)\b", re.IGNORECASE
)
PASSIVE_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?\w+(?:ed|wn|ne|de|it|ut|ilt|ten|ade)\b(?:\s+by\b)?",
    re.IGNORECASE,
)
NOMINAL_RE = re.compile(r"\b\w+(?:tion|sion|ment|ness|ity|ance|ence)\b", re.IGNORECASE)
LY_ADVERB_RE = re.compile(r"\b\w+ly\b", re.IGNORECASE)
FIRST_PERSON_RE = re.compile(
    r"\b(?:I|I'm|I've|I'll|I'd|me|my|mine|we|our|us)\b"
)
MARKDOWN_RE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|__[^_]+__|#{1,6}\s|^\s*[-*+•]\s|^\s*\d+\.\s|\[[^\]]+\]\([^)]+\)|```|>\s|\|)", re.MULTILINE)
BOLD_LEADIN_RE = re.compile(r"\*\*[^*]+:\*\*")


def split_sentences(text):
    parts = SENT_SPLIT_RE.split(text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def words(text):
    return WORD_RE.findall(text)


def _norm(v, lo, hi):
    if hi == lo:
        return 0.0
    t = (v - lo) / (hi - lo)
    return max(0.0, min(1.0, t))


def perplexity_proxy(text):
    import statistics

    ranks = word_ranks()
    ws = words(text.lower())
    if len(ws) < 5:
        return {"mean_surprisal_bits": None, "score": 0.5, "note": "too short"}
    surps = []
    for w in ws:
        r = ranks.get(w, 30000)
        surps.append(min(16.0, max(1.0, _rank_surprisal(r))))
    mean = sum(surps) / len(surps)
    std = statistics.pstdev(surps) if len(surps) > 1 else 0.0
    rare_share = sum(1 for w, s in zip(ws, surps) if s > 10.55 and w not in AI_WORDS) / len(surps)
    bigrams = Counter(zip(ws[:-1], ws[1:]))
    repeated = sum(c for c in bigrams.values() if c > 1)
    rep_rate = repeated / max(1, len(ws) - 1)
    ai_score = 0.62 * _norm(rare_share, 0.30, 0.10) + 0.38 * _norm(rep_rate, 0.005, 0.06)
    return {
        "rare_token_share": round(rare_share, 3),
        "surprisal_std": round(std, 2),
        "bigram_repetition_rate": round(rep_rate, 4),
        "score": round(min(1.0, ai_score), 3),
    }


def _rank_surprisal(rank):
    import math
    return math.log2(rank + 3)


def burstiness(text):
    sents = split_sentences(text)
    if len(sents) < 3:
        return {"cv": None, "score": 0.5, "note": "too few sentences"}
    lengths = [len(words(s)) for s in sents]
    mean = sum(lengths) / len(lengths)
    if mean == 0:
        return {"cv": None, "score": 0.5}
    var = sum((l - mean) ** 2 for l in lengths) / len(lengths)
    cv = (var ** 0.5) / mean
    ai_score = _norm(cv, 0.70, 0.22)
    return {"cv": round(cv, 3), "mean_sentence_words": round(mean, 1), "score": round(ai_score, 3)}


def stylometry(text):
    ws = words(text)
    n = max(1, len(ws))
    sents = split_sentences(text)
    if n < 120:
        return {"score": 0.35, "note": "too short for stable stylometry", "per_1000_words": {}, "active": False}

    contr = len(CONTRACTION_RE.findall(text))
    first_p = len(FIRST_PERSON_RE.findall(text))
    ly = len(LY_ADVERB_RE.findall(text))
    nominal = len(NOMINAL_RE.findall(text))
    passive = len(PASSIVE_RE.findall(text))
    em_dash = text.count("\u2014") + text.count("\u2013")
    semi = text.count(";")
    colon = text.count(":")
    comma = text.count(",")
    exclam = text.count("!")
    quest = text.count("?")

    openers = [s.split()[0].lower() if s.split() else "" for s in sents]
    opener_rep = sum(1 for i in range(1, len(openers)) if openers[i] == openers[i - 1] and openers[i])
    conn_starts = sum(1 for s in sents if CONNECTIVE_STARTS.match(s))

    def per1000(c):
        return round(1000 * c / n, 2)

    human_norms = {
        "contractions": (2.0, 14.0),
        "first_person": (2.0, 20.0),
        "passive": (0.0, 4.0),
        "em_dash": (0.0, 1.5),
        "semicolon": (0.0, 1.0),
        "nominalizations": (8.0, 30.0),
        "ly_adverbs": (2.0, 12.0),
    }
    ai_score = 0.0
    ai_score += 0.18 * _norm(human_norms["contractions"][1] - per1000(contr), 14.0, 4.0)
    ai_score += 0.12 * _norm(human_norms["first_person"][1] - per1000(first_p), 20.0, 2.0)
    ai_score += 0.12 * _norm(per1000(passive), 0.0, 8.0)
    ai_score += 0.10 * _norm(per1000(em_dash), 2.0, 5.0)
    ai_score += 0.08 * _norm(per1000(semi), 1.5, 4.0)
    ai_score += 0.10 * _norm(per1000(nominal), 15.0, 40.0)
    ai_score += 0.08 * _norm(per1000(ly), 8.0, 20.0)
    if sents:
        ai_score += 0.12 * _norm(opener_rep / len(sents), 0.05, 0.35)
        ai_score += 0.10 * _norm(conn_starts / len(sents), 0.05, 0.30)
    ai_score += 0.10 * _norm(per1000(comma), 85.0, 45.0)
    return {
        "per_1000_words": {
            "contractions": per1000(contr),
            "first_person": per1000(first_p),
            "ly_adverbs": per1000(ly),
            "nominalizations": per1000(nominal),
            "passive_voice": per1000(passive),
            "em_dash": per1000(em_dash),
            "semicolon": per1000(semi),
            "colon": per1000(colon),
            "comma": per1000(comma),
            "exclamation": per1000(exclam),
            "question": per1000(quest),
        },
        "repeated_opener_rate": round(opener_rep / max(1, len(sents)), 3),
        "connective_opener_rate": round(conn_starts / max(1, len(sents)), 3),
        "score": round(min(1.0, ai_score), 3),
    }


VENDOR_WORDS = {
    "gpt": ["delve", "tapestry", "realm", "navigate", "underscore", "testament", "pivotal", "foster", "landscape", "leverage", "robust", "seamless", "multifaceted", "holistic", "revolutionize", "unlock"],
    "claude": ["nuance", "nuanced", "acknowledge", "warranty"],
    "gemini": ["multifaceted", "holistic", "crucial", "significant"],
    "generic": ["moreover", "furthermore", "additionally", "consequently", "notably", "comprehensive", "ensure", "utilize", "facilitate", "paramount", "plethora", "myriad"],
}


def ai_phrase_density(text):
    low = text.lower()
    total = 0
    hits = []
    for pat, _ in AI_PHRASES:
        for m in re.finditer(pat, low, re.IGNORECASE):
            total += 1
            hits.append(m.group(0)[:60])
    vendor = {k: 0 for k in VENDOR_WORDS}
    n_ws = words(low)
    for w in n_ws:
        for vendor_name, vocab in VENDOR_WORDS.items():
            if w in vocab:
                vendor[vendor_name] += 1
    known_words = sum(1 for w in n_ws if w in AI_WORDS)
    total += known_words
    n = max(1, len(n_ws))
    density = 1000 * total / n
    ai_score = _norm(density, 2.0, 20.0)
    return {
        "matches": total,
        "per_1000_words": round(density, 2),
        "sample_hits": hits[:8],
        "vendor_fingerprint_tally": vendor,
        "score": round(ai_score, 3),
    }


def formatting_fingerprint(text):
    md = len(MARKDOWN_RE.findall(text))
    bold_leadins = len(BOLD_LEADIN_RE.findall(text))
    emoji = len(EMOJI_RE.findall(text))
    unicode_susp = sum(text.count(k) for k in UNICODE_FIXES if k in "\u200b\u200c\u200d\ufeff\u0430\u0435\u043e\u0440\u0441")
    bullets = len(re.findall(r"^\s*(?:[-*+•]|\d+\.)\s", text, re.MULTILINE))
    headers = len(re.findall(r"^#{1,6}\s", text, re.MULTILINE))
    ai_score = _norm(md + bullets * 1.5 + headers * 2 + emoji * 2 + unicode_susp * 3, 1.0, 15.0)
    return {
        "markdown_tokens": md,
        "bold_leadins": bold_leadins,
        "emoji": emoji,
        "bullets": bullets,
        "headers": headers,
        "suspicious_unicode": unicode_susp,
        "score": round(ai_score, 3),
    }


def detectgpt_proxy(text):
    sents = split_sentences(text)
    if len(sents) < 3:
        return {"score": 0.5, "note": "too short"}
    ws = [words(s.lower()) for s in sents]
    lens = [len(w) for w in ws]
    spread = (max(lens) - min(lens)) / max(1, sum(lens) / len(lens))
    trigrams = Counter()
    total = 0
    for w in ws:
        for i in range(len(w) - 2):
            trigrams[tuple(w[i : i + 3])] += 1
            total += 1
    collision = sum(c - 1 for c in trigrams.values() if c > 1) / max(1, total)
    ai_score = 0.6 * _norm(spread, 1.2, 0.2) + 0.4 * _norm(collision, 0.01, 0.10)
    return {
        "sentence_length_spread": round(spread, 3),
        "trigram_collision_rate": round(collision, 4),
        "score": round(min(1.0, ai_score), 3),
    }


def function_word_ratio(text):
    from .semantics import _expand_contractions

    ws = [w.lower() for w in WORD_RE.findall(_expand_contractions(text))]
    if len(ws) < 40:
        return {"ratio": None, "score": 0.35, "note": "too short", "active": False}
    r = sum(1 for w in ws if w in FUNCTION_WORDS) / len(ws)
    return {"ratio": round(r, 3), "score": round(_norm(r, 0.50, 0.36), 3)}


def word_length_profile(text):
    ws = WORD_RE.findall(text)
    if len(ws) < 40:
        return {"mean_chars": None, "score": 0.35, "note": "too short", "active": False}
    m = sum(len(w) for w in ws) / len(ws)
    return {"mean_chars": round(m, 2), "score": round(_norm(m, 5.0, 6.0), 3)}


def ngram_repetition(text):
    ws = [w.lower() for w in WORD_RE.findall(text)]
    if len(ws) < 150:
        return {"trigram_repeat_rate": None, "score": 0.35, "note": "too short", "active": False}
    tris = Counter(tuple(ws[i : i + 3]) for i in range(len(ws) - 2))
    repeated = sum(c for c in tris.values() if c > 1)
    rate = repeated / max(1, len(ws) - 2)
    return {"trigram_repeat_rate": round(rate, 4), "score": round(_norm(rate, 0.03, 0.002), 3)}


def classifier_proxy(text):
    per = perplexity_proxy(text)
    bur = burstiness(text)
    sty = stylometry(text)
    phr = ai_phrase_density(text)
    dg = detectgpt_proxy(text)
    fwr = function_word_ratio(text)
    wlp = word_length_profile(text)
    ngm = ngram_repetition(text)
    parts = [
        ("ai_phrases", 0.20, phr),
        ("perplexity", 0.16, per),
        ("burstiness", 0.16, bur),
        ("stylometry", 0.16, sty),
        ("detectgpt_proxy", 0.10, dg),
        ("function_word_ratio", 0.10, fwr),
        ("word_length", 0.06, wlp),
        ("ngram_repetition", 0.06, ngm),
    ]
    active = [(name, wt, m.get("score", 0.5)) for name, wt, m in parts if m.get("active", True)]
    total_w = sum(wt for _, wt, _ in active) or 1.0
    weighted = sum(wt * sc for _, wt, sc in active) / total_w
    return {
        "weights": {name: round(wt / total_w, 3) for name, wt, _ in active},
        "active_scorers": [name for name, _, _ in active],
        "score": round(min(1.0, weighted), 3),
    }


def watermark_resistance(text):
    ranks = word_ranks()
    ws = words(text)
    if not ws:
        return {"score": 0.5, "note": "empty"}
    substitutable = sum(1 for w in ws if w.lower() not in ranks or ranks[w.lower()] > 800)
    proper_or_numeric = sum(1 for w in ws if not w.isalpha())
    coverage = substitutable / max(1, len(ws))
    return {
        "paraphrase_eligible_token_rate": round(coverage, 3),
        "estimated_greenlist_signal_survival": round(max(0.0, 1.0 - coverage * 1.2), 3),
        "estimated_synthid_signal_survival": round(max(0.0, 0.55 - coverage * 0.8), 3),
        "note": "generative watermarks (SynthID, green/red-list) are statistical token biases; heavy synonym substitution and reordering destroy the g-value/z-score correlation",
        "score": round(max(0.0, 1.0 - coverage), 3),
    }


HEURISTIC_NOTE = (
    "internal style heuristic, not a validated AI detector; "
    "calibrated against public research on detector signals, treat as advisory"
)


def full_analysis(text):
    return {
        "ai_likelihood_overall": overall_score(text),
        "kind": "internal_heuristic",
        "note": HEURISTIC_NOTE,
        "perplexity": perplexity_proxy(text),
        "burstiness": burstiness(text),
        "stylometry": stylometry(text),
        "ai_phrases": ai_phrase_density(text),
        "detectgpt_proxy": detectgpt_proxy(text),
        "classifier_proxy": classifier_proxy(text),
        "formatting": formatting_fingerprint(text),
        "watermark_resistance": watermark_resistance(text),
        "function_word_ratio": function_word_ratio(text),
        "word_length": word_length_profile(text),
        "ngram_repetition": ngram_repetition(text),
    }


def overall_score(text):
    parts = [
        (0.22, classifier_proxy(text)),
        (0.13, perplexity_proxy(text)),
        (0.13, burstiness(text)),
        (0.14, stylometry(text)),
        (0.13, ai_phrase_density(text)),
        (0.05, detectgpt_proxy(text)),
        (0.03, formatting_fingerprint(text)),
        (0.07, function_word_ratio(text)),
        (0.05, word_length_profile(text)),
        (0.05, ngram_repetition(text)),
    ]
    active = [(wt, m.get("score", 0.5)) for wt, m in parts if m.get("active", True)]
    total_w = sum(wt for wt, _ in active) or 1.0
    val = sum(wt * sc for wt, sc in active) / total_w
    return round(min(100.0, max(0.0, val * 100)), 1)
