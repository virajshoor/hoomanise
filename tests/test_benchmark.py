import json
import os

import pytest

os.environ.setdefault("APP_ENV", "test")

from app.engine import analysis, pipeline

# Fixed benchmark corpus: genres x register. AI-flavored inputs per genre plus
# genuinely human samples. Used to track quality over time.

CORPUS = {
    "essay": (
        "In today's fast-paced digital landscape, it is important to recognize that "
        "social media plays a crucial role in shaping public opinion. Moreover, "
        "platforms utilize sophisticated algorithms to curate content for billions of "
        "users. Furthermore, this unprecedented level of connectivity fosters global "
        "communication. In conclusion, navigating the complexities of the digital age "
        "requires a multifaceted approach that is not just technical but ethical."
    ),
    "email": (
        "Dear Team, I hope this email finds you well. Additionally, I wanted to reach "
        "out regarding the quarterly objectives. Furthermore, it is important to note "
        "that we must leverage our resources effectively. Moreover, please do not "
        "hesitate to reach out should you have any questions. Best regards."
    ),
    "casual": (
        "So basically, streaming services have totally revolutionized the way we "
        "consume entertainment. Moreover, there are a plethora of options available. "
        "Additionally, it is worth noting that binge-watching has become the norm. "
        "In conclusion, the landscape of entertainment is constantly evolving."
    ),
    "technical": (
        "It is important to note that microservices architecture enhances scalability. "
        "Moreover, each service can be deployed independently, which facilitates "
        "continuous delivery. Additionally, robust API gateways ensure seamless "
        "communication between components. Furthermore, containerization technologies "
        "like Docker streamline the deployment process. In conclusion, organizations "
        "should utilize these cutting-edge patterns to optimize their infrastructure."
    ),
    "marketing": (
        "Are you ready to unlock the full potential of your business? Our "
        "cutting-edge solution empowers teams to streamline workflows and boost "
        "productivity. Moreover, our seamless integration ensures a hassle-free "
        "experience. Don't miss out on this game-changing opportunity — take the "
        "leap today and embark on a journey towards unprecedented success."
    ),
    "human_casual_1": (
        "I didn't expect much from the new update. It shipped on a Tuesday, quietly, "
        "no blog post, nothing. My colleague flagged a bug within an hour — photo "
        "exports came out upside down. Cute. The team patched it by Wednesday "
        "morning, and to their credit, they didn't pretend it was a feature."
    ),
    "human_email_1": (
        "Hi Sam, quick one — can you move Friday's sync to 2pm? I've got a doctor's "
        "appointment at noon and won't make it back in time. If 2 doesn't work for "
        "the folks in Berlin, push it to Monday, whatever's easier. Thanks!"
    ),
    "human_technical_1": (
        "The retry logic has a subtle bug. When the connection drops mid-handshake, "
        "we retry with the same request ID, and the server treats it as a duplicate "
        "and returns 409. I think we need to regenerate the ID on retry, or at least "
        "add an idempotency key header. Repro is in the ticket."
    ),
}

# Known old failures — must never reappear (regression set)
REGRESSION_INPUTS = [
    (
        "However, it is major to recognize that this matters.",
        ["major to"],
    ),
    (
        "Issues such as data privacy, algorithmic bias, job displacement.",
        ["job displacement. And ethical"],
    ),
    (
        "They simplify processes, enhance efficiency.",
        [", enhance efficiency."],
    ),
    (
        "Also AI-powered systems analyze data.",
        ["Also AI-"],
    ),
]


@pytest.mark.parametrize("genre", list(CORPUS.keys()))
def test_corpus_semantic_preservation(genre):
    text = CORPUS[genre]
    h, meta = pipeline.humanize(text, "professional", 70, 100 + hash(genre) % 50)
    assert meta["semantic_check"] == "passed", (genre, meta.get("rejections"))
    import re
    assert set(re.findall(r"\d+", text)) <= set(re.findall(r"\d+", h))


@pytest.mark.parametrize("genre", ["essay", "email", "casual", "technical", "marketing"])
def test_corpus_ai_input_improves(genre):
    text = CORPUS[genre]
    before = analysis.overall_score(text)
    h, meta = pipeline.humanize(text, "professional", 70, 200)
    after = analysis.overall_score(h)
    assert meta["semantic_check"] == "passed"
    assert after <= before + 2, f"{genre}: {before} -> {after}"


def test_corpus_human_inputs_stay_stable():
    for genre in ("human_casual_1", "human_email_1", "human_technical_1"):
        text = CORPUS[genre]
        h, meta = pipeline.humanize(text, "casual", 70, 300)
        assert meta["already_human"] is True, f"{genre} flagged as AI (score {meta.get('baseline_style_score')})"
        assert meta["similarity"] > 0.8


def test_grammar_gate_clean_output():
    from app.engine.grammar import grammar_check

    for genre, text in CORPUS.items():
        h, meta = pipeline.humanize(text, "professional", 70, 400)
        if meta.get("already_human"):
            continue
        problems = grammar_check(h)
        assert not problems, f"{genre}: {problems}"


def test_structural_distance_minimum():
    from app.engine.structure import structural_distance

    for genre, text in CORPUS.items():
        if genre.startswith("human"):
            continue
        h, meta = pipeline.humanize(text, "professional", 70, 500)
        assert meta.get("structural_distance", 0) >= 0.08, f"{genre}: too timid"


def test_regression_known_failures():
    import re

    for text, bad_patterns in REGRESSION_INPUTS:
        for seed in (1, 2, 3, 4, 5):
            h, meta = pipeline.humanize(text, "professional", 70, seed)
            for bad in bad_patterns:
                assert bad not in h, f"seed {seed} produced {bad!r}"
            assert meta["semantic_check"] == "passed"


def test_similarity_window_at_70():
    text = CORPUS["essay"]
    h, meta = pipeline.humanize(text, "professional", 70, 600)
    assert 0.70 <= meta["similarity"] <= 0.95, meta["similarity"]


def test_tier_monotonicity():
    sims = {}
    for it in (20, 50, 70, 90):
        h, meta = pipeline.humanize(CORPUS["essay"], "professional", it, 700)
        sims[it] = (meta["similarity"], meta.get("tier"))
    assert sims[20][1] == "low" and sims[50][1] == "medium"
    assert sims[70][1] == "high" and sims[90][1] == "high"
