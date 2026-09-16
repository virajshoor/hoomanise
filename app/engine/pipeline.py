import difflib
import random
import re

from . import analysis, transforms
from .resources import (
    AI_PHRASES,
    AI_WORDS,
    DROPPABLE_WORDS,
    ASIDES_CASUAL,
    ASIDES_FORMAL,
    MARKERS_CASUAL,
    MARKERS_FORMAL,
    NOMINALIZATION_FIXES,
    RARE_SWAPS,
    STRUCTURE_PATTERNS,
    SYNONYMS,
    STOPWORDS,
    WORD_RE,
)

PRESETS = ["casual", "professional", "academic"]

REGISTER = {
    "casual": {
        "contraction_factor": 1.0,
        "rare_mode": "all",
        "markers": MARKERS_CASUAL,
        "asides": ASIDES_CASUAL,
    },
    "professional": {
        "contraction_factor": 0.3,
        "rare_mode": "neutral",
        "markers": MARKERS_FORMAL,
        "asides": ASIDES_FORMAL,
    },
    "academic": {
        "contraction_factor": 0.0,
        "rare_mode": "none",
        "markers": MARKERS_FORMAL,
        "asides": [],
    },
}

TIERS = {"low": (0, 33), "medium": (34, 66), "high": (67, 100)}
TARGET_SIM = {"low": (0.88, 0.99), "medium": (0.80, 0.95), "high": (0.72, 0.93)}
MIN_DISTANCE = {"low": 0.03, "medium": 0.08, "high": 0.14}


def tier_of(intensity: int) -> str:
    for name, (lo, hi) in TIERS.items():
        if lo <= intensity <= hi:
            return name
    return "medium"


def _allowlist():
    words = set()
    for _, reps in AI_PHRASES:
        for t in reps:
            words.update(w.lower() for w in WORD_RE.findall(t))
    for _, reps in STRUCTURE_PATTERNS:
        for t in reps:
            words.update(re.sub(r"\{\d\}", " ", t).lower().split())
    for _, reps in NOMINALIZATION_FIXES:
        for t in reps:
            words.update(re.sub(r"\{\d\}|\\\\\d", " ", t).lower().split())
    for alts in AI_WORDS.values():
        for a in alts:
            words.update(w.lower() for w in WORD_RE.findall(a))
    for alts in SYNONYMS.values():
        for a in alts:
            words.update(w.lower() for w in WORD_RE.findall(a))
    for alts in RARE_SWAPS.values():
        for a in alts:
            words.update(w.lower() for w in WORD_RE.findall(a))
    for lst in (MARKERS_CASUAL, MARKERS_FORMAL, ASIDES_CASUAL, ASIDES_FORMAL):
        for m in lst:
            words.update(w.lower().strip(",") for w in WORD_RE.findall(m))
    for table in (AI_WORDS, SYNONYMS, RARE_SWAPS):
        words.update(table.keys())
    for pat, _ in AI_PHRASES:
        words.update(_pattern_words(pat))
    words.update({"and", "but", "so", "though", "although", "because", "while", "which", "even", "still", "yet", "plus", "besides", "also", "fact", "practice", "broadly", "speaking", "cases", "part", "instead", "rather"})
    return words



def _pattern_words(pat: str) -> set[str]:
    cleaned = re.sub(r"\[([A-Za-z])([A-Za-z])\]", r"\2", pat)
    cleaned = re.sub(r"\[[^\]]*\\[^\]]*\]", " ", cleaned)
    cleaned = cleaned.replace("\\b", " ").replace("\\s", " ").replace("\\", " ")
    cleaned = re.sub(r"[?+*\[\]{}()|.^$]", " ", cleaned)
    return {w.lower().strip("'-") for w in WORD_RE.findall(cleaned) if len(w) > 2}

ALLOWLIST = _allowlist()

DROPPABLE = set(DROPPABLE_WORDS)
for _k in AI_WORDS:
    DROPPABLE.add(_k)
for _alts in AI_WORDS.values():
    for _a in _alts:
        DROPPABLE.update(w.lower() for w in WORD_RE.findall(_a))
for _k, _alts in SYNONYMS.items():
    DROPPABLE.add(_k)
    for _a in _alts:
        DROPPABLE.update(w.lower() for w in WORD_RE.findall(_a))
for _k, _alts in RARE_SWAPS.items():
    DROPPABLE.add(_k)
    for _a in _alts:
        DROPPABLE.update(w.lower() for w in WORD_RE.findall(_a))
for _lst in (MARKERS_CASUAL, MARKERS_FORMAL, ASIDES_CASUAL, ASIDES_FORMAL):
    for _m in _lst:
        DROPPABLE.update(w.lower().strip(",") for w in WORD_RE.findall(_m))
for _pat, _reps in AI_PHRASES:
    for _t in _reps:
        DROPPABLE.update(w.lower() for w in WORD_RE.findall(_t))
    DROPPABLE.update(_pattern_words(_pat))
for _pat, _reps in NOMINALIZATION_FIXES:
    for _t in _reps:
        DROPPABLE.update(w.lower() for w in re.sub(r"\{\d\}|\\\\\d", " ", _t).split())
    DROPPABLE.update(_pattern_words(_pat))
for _pat, _reps in STRUCTURE_PATTERNS:
    for _t in _reps:
        DROPPABLE.update(w.lower() for w in re.sub(r"\{\d\}", " ", _t).split())
    DROPPABLE.update(_pattern_words(_pat))


def _similarity(a: str, b: str) -> float:
    aw, bw = " ".join(WORD_RE.findall(a.lower())), " ".join(WORD_RE.findall(b.lower()))
    return round(difflib.SequenceMatcher(None, aw, bw, autojunk=False).ratio(), 3)


def _run_pass(text, preset, intensity, seed, preserve_formatting, structural=False):
    reg = REGISTER[preset]
    tier = tier_of(intensity)
    rng = random.Random(seed)
    prob = min(1.0, intensity / 100 * 1.15)

    x = transforms.normalize_unicode(text)
    spans = None
    if preserve_formatting:
        x, spans = transforms.protect_spans(x)
    else:
        x = transforms.strip_emoji(x)
        x = transforms.strip_markdown(x, rng, prob)

    if preset != "casual":
        x = transforms.strip_casual_markers(x, rng, min(1.0, prob + 0.25))
    x = transforms.scrub_ai_phrases(x, rng, min(1.0, prob + 0.25))
    x = transforms.scrub_ai_words(x, rng, min(1.0, prob + 0.2))

    if tier != "low":
        x = transforms.denominalize(x, rng, prob * 0.7)
        x = transforms.break_structure_patterns(x, rng, prob)
        x = transforms.lexical_jitter(x, rng, prob * 0.55)
        rare_factor = {"all": 0.35, "neutral": 0.22, "none": 0.0}[reg["rare_mode"]]
        if rare_factor > 0:
            x = transforms.rare_word_spice(x, rng, prob * rare_factor, preset)

    c_prob = prob * reg["contraction_factor"]
    x = transforms.inject_contractions(x, rng, c_prob)
    x = transforms.scrub_ai_phrases(x, rng, min(1.0, prob + 0.25))
    if structural and tier != "low":
        x = transforms.flip_voice(x, rng, prob * 0.6)
        x = transforms.invert_one_of(x, rng, prob * 0.4)

    def _para(t):
        t = transforms.repair_fragments(t, rng)
        if structural and tier == "high":
            t = transforms.front_adverbial(t, rng, prob * 0.5)
        if tier == "low":
            t = transforms.rewrite_burstiness(t, rng, prob * 0.5)
        else:
            t = transforms.rewrite_burstiness(t, rng, prob)
        t = transforms.diversify_openers(t, rng, prob)
        t = transforms.humanize_punctuation(t, rng, min(1.0, prob + 0.3))
        if tier == "high" and reg["asides"]:
            t = transforms.inject_asides(t, rng, prob * 0.4, reg["asides"])
        if tier != "low":
            t = transforms.add_discourse_markers(
                t, rng, prob * (1.0 if preset == "casual" else 0.6), reg["markers"]
            )
        if structural and tier == "high":
            t = transforms.reorder_sentences(t, rng, prob * 0.35)
        return t

    if preserve_formatting:
        x = transforms.map_paragraphs(x, _para)
    else:
        x = _para(x)

    if preset == "casual" and tier == "high":
        x = transforms.casual_touches(x, rng, prob)

    if structural and tier == "high" and preserve_formatting:
        x = transforms.split_paragraphs(x, rng, prob * 0.3)
    x = transforms.cap_markers(x)
    x = transforms.tidy(x)
    if spans:
        x = transforms.restore_spans(x, spans)
    return x


def humanize(text: str, preset: str = "casual", intensity: int = 70, seed: int | None = None,
             preserve_formatting: bool = True, human_threshold: float = 40.0):
    intensity = max(0, min(100, int(intensity)))
    seed = seed if seed is not None else 0
    rng = random.Random(seed)

    baseline = analysis.overall_score(text)

    def _minimal(reason):
        out = transforms.repair_only(transforms.normalize_unicode(text), rng)
        return out, {
            "already_human": baseline < human_threshold,
            "semantic_check": "passed" if not reason else "rejected_all_attempts",
            "rejections": reason if isinstance(reason, list) else [],
            "attempts": 0,
            "similarity": _similarity(text, out),
            "baseline_style_score": baseline,
            "tier": tier_of(intensity),
        }

    if baseline < human_threshold:
        if baseline < human_threshold * 0.6:
            out, meta = _minimal(None)
            meta["already_human"] = True
            return out, meta
        candidate = _run_pass(text, preset, 12, seed, preserve_formatting)
        problems = analysis_pre_check(text, candidate)
        if not problems:
            return candidate, {
                "already_human": True,
                "semantic_check": "passed",
                "rejections": [],
                "attempts": 1,
                "similarity": _similarity(text, candidate),
                "baseline_style_score": baseline,
                "tier": "low",
            }
        out, meta = _minimal(problems)
        meta["already_human"] = True
        return out, meta

    from .grammar import grammar_check
    from .pos_grammar import pos_issues
    from .structure import (
        lexical_diversity_regression,
        structural_distance,
    )

    tier = tier_of(intensity)
    reg = REGISTER[preset]

    plans = [
        (seed, intensity, False),
        (seed + 1, max(35, intensity - 15), True),
        (seed + 2, min(95, intensity + 12), True),
        (seed + 3, max(25, intensity - 30), False),
        (seed + 4, max(20, intensity - 50), False),
    ]
    if tier == "low":
        plans = plans[:2]
    if len(text) < 600 and tier != "low":
        # short cliché-dense texts: soften every plan so similarity stays in window
        plans = [(sd, max(20, int(it * 0.75)), st) for sd, it, st in plans]

    records = []
    for i, (sd, it, structural) in enumerate(plans):
        candidate = _run_pass(text, preset, max(5, it), sd, preserve_formatting, structural=structural)
        problems = analysis_pre_check(text, candidate) + grammar_check(candidate)
        problems += lexical_diversity_regression(text, candidate)
        records.append({
            "output": candidate,
            "problems": problems,
            "score": analysis.overall_score(candidate),
            "similarity": _similarity(text, candidate),
            "distance": structural_distance(text, candidate),
            "attempt": i + 1,
            "structural": structural,
        })

    eligible = [r for r in records if not r["problems"]]
    if eligible and tier != "low":
        lo0, hi0 = TARGET_SIM[tier]
        if not any(lo0 <= r["similarity"] <= hi0 for r in eligible):
            for sd, it, st in (
                (seed + 10, max(20, int(intensity * 0.35)), False),
                (seed + 11, min(95, int(intensity * 1.15)), True),
            ):
                cand = _run_pass(text, preset, it, sd, preserve_formatting, structural=st)
                probs = analysis_pre_check(text, cand) + grammar_check(cand)
                probs += lexical_diversity_regression(text, cand)
                records.append({
                    "output": cand,
                    "problems": probs,
                    "score": analysis.overall_score(cand),
                    "similarity": _similarity(text, cand),
                    "distance": structural_distance(text, cand),
                    "attempt": len(records) + 1,
                    "structural": st,
                })
            eligible = [r for r in records if not r["problems"]]

    if eligible:
        lo, hi = TARGET_SIM[tier]
        mid = (lo + hi) / 2

        def rank(r):
            sim_pen = max(0.0, lo - r["similarity"], r["similarity"] - hi) * 3.5
            dist_pen = max(0.0, MIN_DISTANCE[tier] - r["distance"]) * 1.8
            return sim_pen + dist_pen + r["score"] * 0.25

        eligible.sort(key=rank)
        best = None
        for r in eligible[:3]:
            pos_problems = pos_issues(r["output"])
            if not pos_problems:
                best = r
                break
        if best is None:
            best = eligible[0]
        return best["output"], {
            "already_human": False,
            "semantic_check": "passed",
            "rejections": [],
            "attempts": len(records),
            "similarity": best["similarity"],
            "structural_distance": best["distance"],
            "baseline_style_score": baseline,
            "tier": tier,
        }

    best = min(records, key=lambda r: (len(r["problems"]), -r["distance"]))
    out, meta = _minimal(best["problems"])
    meta["fallback_score"] = best["score"]
    meta["attempts"] = len(records)
    return out, meta


def analysis_pre_check(original: str, rewritten: str) -> list[str]:
    from .semantics import semantic_check

    return semantic_check(original, rewritten, ALLOWLIST, DROPPABLE)


METHODS = [
    {"id": "perplexity", "name": "Perplexity (token predictability)", "counter": "Rare-word substitution, lexical jitter"},
    {"id": "burstiness", "name": "Burstiness (sentence-length variance)", "counter": "Meaning-preserving sentence merge/split"},
    {"id": "stylometry", "name": "Stylometry (function words, punctuation, voice)", "counter": "Contractions (register-scaled), passive fixes, punctuation humanization"},
    {"id": "ai_phrases", "name": "AI-phrase / n-gram density", "counter": "Vendor-phrase scrubbing, tricolon softening"},
    {"id": "detectgpt", "name": "DetectGPT-style likelihood curvature", "counter": "Register-appropriate rewrites push text off local maxima"},
    {"id": "classifier", "name": "Supervised classifier stylometric hybrid", "counter": "Targets top features incl. function-word ratio, word length, n-gram repetition"},
    {"id": "formatting", "name": "Formatting fingerprint", "counter": "Formatting preserved by default; stripped only when explicitly disabled"},
    {"id": "watermark", "name": "Generative watermarks (SynthID, green/red-list)", "counter": "Paraphrase coverage destroys g-value/z-score correlation"},
    {"id": "vendor_fingerprint", "name": "Vendor accent (OpenAI vs Anthropic vs Google)", "counter": "Vendor vocabulary and punctuation habits scrubbed within register"},
]
