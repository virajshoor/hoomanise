import json
import os

import pytest

os.environ.setdefault("APP_ENV", "test")

from app.engine import analysis, pipeline
from app.engine.semantics import semantic_check

AI_TEXT = (
    "In today's fast-paced digital landscape, it is important to note that leveraging "
    "robust solutions is paramount. Moreover, organizations must delve into the myriad "
    "of opportunities. Additionally, seamless integration is essential. Furthermore, "
    "comprehensive analytics foster growth and innovation. In conclusion, a "
    "multifaceted approach is a testament to success."
)

CASUAL_HUMAN_TEXT = (
    "I didn't expect much from the update, honestly. It shipped on a Tuesday, no blog "
    "post, nothing. My colleague flagged a bug within an hour. Cute. The team patched "
    "it by Wednesday morning. I've seen worse launches, far worse. Still, someone "
    "should've caught that in QA. It's a small company; everyone wears five hats."
)

FORMAL_TEXT = (
    "Artificial intelligence is rapidly transforming the way we live and work with the "
    "world around us. Right now, AI has emerged as a powerful tool that offers a wide "
    "range of benefits across plenty of industries. Frankly, from healthcare and "
    "education to finance and transportation, artificial intelligence is helping "
    "organizations simplify processes, enhance efficiency. Also AI-powered systems can "
    "analyze vast amounts of data in a matter of seconds, enabling businesses to "
    "identify patterns and uncover valuable insights that may otherwise go unnoticed. "
    "However, it's major to recognize that artificial intelligence is not without its "
    "challenges. Issues such as data privacy, algorithmic bias, job displacement. And "
    "ethical concerns must be carefully considered as the technology continues to evolve."
)


def test_meaning_preserved_on_flagged_text():
    h, meta = pipeline.humanize(FORMAL_TEXT, "professional", 70, 42)
    assert meta["semantic_check"] == "passed"
    problems = semantic_check(FORMAL_TEXT, h, pipeline.ALLOWLIST, pipeline.DROPPABLE)
    assert problems == []


def test_numbers_dates_names_survive():
    text = (
        "In 2024, OpenAI released GPT-4 to 100 million users, cutting costs by 90 "
        "percent. By March 2025, revenue had doubled, and Anthropic, Google, and Meta "
        "followed with 3 major releases. Not everyone agrees with this pace."
    )
    h, meta = pipeline.humanize(text, "professional", 80, 7)
    assert meta["semantic_check"] == "passed"
    import re
    assert set(re.findall(r"\d+", text)) <= set(re.findall(r"\d+", h))
    for name in ("OpenAI", "GPT-4", "Anthropic", "Google", "Meta"):
        assert name in h


def test_negations_not_flipped():
    text = (
        "Moreover, it is important to note that this approach is not without risks. "
        "Furthermore, the system does not guarantee success, and critics say it will "
        "never replace human judgment in high-stakes decisions."
    )
    h, meta = pipeline.humanize(text, "professional", 70, 11)
    import re
    o = len(re.findall(r"\b(?:not|never|no|without)\b", text, re.I))
    n = len(re.findall(r"\b(?:not|never|no|without)\b", h, re.I))
    assert o == n
    assert meta["semantic_check"] == "passed"


def test_already_human_makes_minimal_changes():
    h, meta = pipeline.humanize(CASUAL_HUMAN_TEXT, "casual", 70, 3)
    assert meta["already_human"] is True
    assert meta["similarity"] > 0.85


def test_double_pass_does_not_destroy_prose():
    once, m1 = pipeline.humanize(AI_TEXT, "casual", 70, 5)
    twice, m2 = pipeline.humanize(once, "casual", 70, 6)
    assert m2["semantic_check"] == "passed"
    assert m2["similarity"] > 0.6
    s1 = analysis.overall_score(once)
    s2 = analysis.overall_score(twice)
    assert s2 <= s1 + 12


def test_formatting_preserved():
    text = (
        "# Getting Started\n\n"
        "First, install the CLI tool from https://example.com/install.\n\n"
        "- **Speed**: runs in `npm install` under 30 seconds\n"
        "- **Cost**: see the pricing table (Smith, 2020)\n\n"
        "The API key format is `hm_xxxx`. See [docs](https://example.com/docs) for details.\n\n"
        "Contact support@example.com or call 555-0199."
    )
    h, meta = pipeline.humanize(text, "professional", 70, 9)
    assert meta["semantic_check"] == "passed"
    assert "# Getting Started" in h
    assert "- **Speed**:" in h
    assert "https://example.com/install" in h
    assert "`npm install`" in h
    assert "(Smith, 2020)" in h
    assert "555-0199" in h


def test_register_no_casual_leakage_into_formal():
    for preset in ("professional", "academic"):
        h, _ = pipeline.humanize(FORMAL_TEXT, preset, 70, 21)
        low = h.lower()
        for leak in ("frankly,", "look,", "plenty of", "kind of", "dirt-cheap", "super "):
            assert leak not in low, f"{preset} leaked '{leak}'"


def test_intensity_tiers_scale_changes():
    lows, meds, highs = [], [], []
    for seed in (1, 2, 3):
        lo, _ = pipeline.humanize(AI_TEXT, "professional", 20, seed)
        med, _ = pipeline.humanize(AI_TEXT, "professional", 55, seed)
        hi, _ = pipeline.humanize(AI_TEXT, "professional", 90, seed)
        lows.append(pipeline._similarity(AI_TEXT, lo))
        meds.append(pipeline._similarity(AI_TEXT, med))
        highs.append(pipeline._similarity(AI_TEXT, hi))
    assert sum(lows) / 3 > sum(meds) / 3 >= sum(highs) / 3 * 0.8


def test_known_artifacts_never_produced():
    import re
    bad_patterns = [
        r"major to", r"Also AI-", r"industriesfrom", r"so however", r"and and",
        r"(?<!and )enhance efficiency\.", r"job displacement\. And", r"gig displacement",
        r"Frankly,", r"plenty of",
    ]
    for seed in range(1, 13):
        h, meta = pipeline.humanize(FORMAL_TEXT, "professional", 70, seed)
        assert meta["semantic_check"] == "passed"
        for bad in bad_patterns:
            assert not re.search(bad, h), f"seed {seed} produced '{bad}'"
