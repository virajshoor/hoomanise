import json
import os
import statistics

import pytest

os.environ.setdefault("APP_ENV", "test")

from app.engine import analysis, pipeline

with open("tests/data/human_samples.json") as f:
    HUMAN_SAMPLES = json.load(f)["samples"]


def test_human_lift_mean_score():
    scores = []
    for i, text in enumerate(HUMAN_SAMPLES):
        score = analysis.overall_score(text)
        scores.append(score)
    mean = statistics.mean(scores)
    assert mean < 30, f"mean human score {mean:.1f} too high (false-positive drift)"
    flagged = [s for s in scores if s > 45]
    assert len(flagged) <= 2, f"{len(flagged)} human samples flagged as AI"


def test_human_samples_get_minimal_changes():
    worst = None
    for i, text in enumerate(HUMAN_SAMPLES):
        h, meta = pipeline.humanize(text, "casual", 70, 1000 + i)
        assert meta["already_human"] is True, f"sample {i} gated as AI (score {meta.get('baseline_style_score')}): {text[:60]!r}"
        sim = meta["similarity"]
        if worst is None or sim < worst[0]:
            worst = (sim, text[:60])
    assert worst[0] > 0.75, f"human sample changed too much: {worst}"


def test_mean_report():
    scores = [analysis.overall_score(t) for t in HUMAN_SAMPLES]
    print(f"\nhuman-lift eval: n={len(scores)} mean={statistics.mean(scores):.1f} "
          f"max={max(scores):.1f} p90={sorted(scores)[int(0.9 * len(scores))]:.1f}")
    assert True
