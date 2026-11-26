import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from guardrail.benchmark import measure
from guardrail.benchmark_score import Observation, Outcome, Rates, Run, score, summary
from guardrail.cli import app
from guardrail.model_review import PROMPT_ID, ResponseUsage

DATASET = Path(__file__).resolve().parents[1] / "benchmarks"
TARGETS = {
    r["id"]: r["target"] for r in yaml.safe_load((DATASET / "labels.yaml").read_text())
}


def finding(check, file, line, category=None):
    return Observation(
        check=check,
        file=file,
        line=line,
        message="Observed problem.",
        severity="warning",
        category=category,
    )


def test_hand_counted_subset():
    duplicate = finding("exception-handling", "dispatch.py", 13)
    alarm = finding(PROMPT_ID, "exports.py", 16, "off-by-one")
    rows = [
        Outcome(id="case-012", findings=[duplicate, duplicate], elapsed_seconds=1),
        Outcome(
            id="case-004",
            elapsed_seconds=2,
            findings=[
                finding(PROMPT_ID, "dispatch.py", 17, "missing-none-handling"),
                finding(PROMPT_ID, "dispatch.py", 16, "off-by-one"),
                finding(PROMPT_ID, "other.py", 16, "missing-none-handling"),
            ],
        ),
        Outcome(id="case-026", findings=[alarm, alarm], elapsed_seconds=3),
    ]
    targets = {r.id: TARGETS[r.id] for r in rows}
    assert score(rows, targets, Rates()) == {
        "caught": 1,
        "targets": 2,
        "recall": 0.5,
        "false_alarms": 1,
        "clean": 1,
        "mean_seconds": 2,
        "mean_usd": 0,
        "unknown_costs": 0,
        "incomplete": 0,
        "unclassified": 0,
    }
    with pytest.raises(ValueError, match="exactly one"):
        score(rows[:-1], targets, Rates())
    with pytest.raises(ValueError, match="exactly one"):
        score([*rows, rows[0]], targets, Rates())


def test_blind_model_observations_and_regenerating_report(tmp_path, monkeypatch):
    base = tmp_path / "inputs" / "dispatch"
    shutil.copytree(DATASET / "inputs/dispatch/base", base / "base")
    (base / "patches").mkdir()
    for ident in ("case-004", "case-012", "case-033"):
        shutil.copy(
            DATASET / f"inputs/dispatch/patches/{ident}.patch", base / "patches"
        )
    calls = []

    async def provider(instructions, hunks, record_usage):
        calls.append(hunks)
        assert all(ident not in instructions + hunks for ident in TARGETS)
        record_usage(ResponseUsage("test-snapshot", 100, 25, 30))
        if "route_summary" in hunks:
            return json.dumps(
                [
                    finding(PROMPT_ID, "dispatch.py", 16).model_dump(
                        exclude_none=True, exclude={"category"}
                    )
                ]
            )
        return "invalid"

    rates = Rates(input=2, cached_input=1, output=5)
    monkeypatch.setenv("GUARDRAIL_MODEL", "test-alias")
    run = measure(tmp_path, True, rates, provider)
    assert len(calls) == 2
    defect, partial, clean = run.model_assisted
    assert defect.requested and defect.cost(rates) == pytest.approx(0.000325)
    assert partial.incomplete and partial.findings[0].check == "exception-handling"
    assert not clean.requested and clean.cost(rates) == 0 and not clean.incomplete
    targets = {r.id: TARGETS[r.id] for r in run.model_assisted}
    assert score(run.model_assisted, targets, rates)["caught"] == 1
    assert score(run.model_assisted, targets, rates)["unclassified"] == 1
    defect.findings[0] = defect.findings[0].model_copy(
        update={"category": "missing-none-handling"}
    )
    assert score(run.model_assisted, targets, rates)["caught"] == 2
    assert score(run.deterministic, targets, rates)["caught"] == 1

    outcomes = tmp_path / "run.json"
    outcomes.write_text(run.model_dump_json(), encoding="utf-8")
    labels = tmp_path / "key.yaml"
    labels.write_text(
        yaml.safe_dump([{"id": k, "target": v} for k, v in targets.items()])
    )
    args = ["benchmark", "score", str(outcomes), "--labels", str(labels)]
    first = CliRunner().invoke(app, args)
    assert first.exit_code == 0, first.output
    assert "2/2" in first.stdout and "test-snapshot" in first.stdout
    assert first.stdout == CliRunner().invoke(app, args).stdout
    assert Run.model_validate_json(outcomes.read_bytes()) == run


def test_incomplete_cost_and_missing_rates_are_unavailable():
    row = Outcome(id="case-004", requested=True, incomplete="Request timed out.")
    assert row.cost(Rates()) is None
    row.usage = ResponseUsage("model", 10, 0, 5)
    assert row.cost(Rates(input=1)) is None
    assert row.cost(Rates(input=1, output=2)) == pytest.approx(0.000020)
    row.usage = ResponseUsage("model", 10, None, 5)
    assert row.cost(Rates(input=1, cached_input=0.5, output=2)) is None
    metrics = score([row], {row.id: TARGETS[row.id]}, Rates())
    assert metrics["mean_usd"] is None and metrics["unknown_costs"] == 1
    assert metrics["mean_seconds"] is None and metrics["incomplete"] == 1


def test_published_report_rebuilds_from_saved_observations():
    results = DATASET / "results"
    run = Run.model_validate_json((results / "run.json").read_bytes())
    assert summary(run, TARGETS) == (results / "summary.md").read_text(encoding="utf-8")


def test_run_saves_partial_provider_metadata_before_reporting_failure(
    tmp_path, monkeypatch
):
    local = Outcome(id="one")
    partial = Outcome(
        id="one",
        requested=True,
        incomplete="Provider returned an incomplete response.",
        usage=ResponseUsage("snapshot", None, None, None),
    )
    run = Run(
        settings={}, rates=Rates(), deterministic=[local], model_assisted=[partial]
    )
    monkeypatch.setattr("guardrail.benchmark.measure", lambda *args: run)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    monkeypatch.setenv("GUARDRAIL_MODEL", "fixture")
    out = tmp_path / "partial.json"
    result = CliRunner().invoke(app, ["benchmark", "run", "--live", "--out", str(out)])
    assert result.exit_code == 3
    saved = Run.model_validate_json(out.read_bytes())
    assert saved == run and saved.model_assisted[0].cost(saved.rates) is None
