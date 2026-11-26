"""Measure the normal saved-patch reviewer on independent benchmark checkouts."""

import asyncio
import hashlib
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from time import perf_counter
from typing import Annotated

import typer
import yaml

from guardrail import codex_cli, model_review
from guardrail.benchmark_score import Observation, Outcome, Rates, Run, summary
from guardrail.patch import PatchInputError
from guardrail.review import DEFAULT_CHECKS, CheckError, run_checks
from guardrail.source import SourceError, patch_source

app = typer.Typer(
    help="Measure review outcomes and score them against benchmark labels."
)


def write_output(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp.write(content.encode("utf-8"))
    Path(temp.name).replace(path)


def input_digest(inputs: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(inputs.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(inputs).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def review_case(patch: Path, checkout: Path, live: bool, provider) -> Outcome:
    result = Outcome(id=patch.stem)

    def record_usage(usage):
        result.usage = usage

    async def measured_request(instructions, hunks):
        result.requested = True
        return await provider(instructions, hunks, record_usage=record_usage)

    start = perf_counter()
    try:
        source = patch_source(patch, checkout)
        if source.skipped_reason:
            raise SourceError(source.skipped_reason)
        findings = run_checks(source, DEFAULT_CHECKS)
        if live:
            model = asyncio.run(
                model_review.review_with_model(source, findings, measured_request)
            )
            result.model_status = model.status
            if model.status.startswith("Model review incomplete"):
                result.incomplete = model.status
            findings.extend(model.findings)
        result.findings = [Observation(**f.model_dump()) for f in findings]
    except (PatchInputError, CheckError, SourceError, OSError) as exc:
        result.incomplete = f"{type(exc).__name__} during review"
    result.elapsed_seconds = round(perf_counter() - start, 6)
    return result


def measure(
    root: Path, live: bool, rates: Rates, provider=codex_cli.codex_review
) -> Run:
    inputs = root.resolve() / "inputs"
    patches = sorted(inputs.glob("*/patches/*.patch"), key=lambda p: p.stem)
    if not patches or len({p.stem for p in patches}) != len(patches):
        raise ValueError(
            "Expected uniquely named patches under inputs/<project>/patches."
        )
    settings = {
        "Started (UTC)": datetime.now(UTC).isoformat(timespec="seconds"),
        "Dataset SHA-256": input_digest(inputs),
        "Runtime": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "Tool versions": ", ".join(
            f"{pkg} {version(pkg)}"
            for pkg in ("ai-code-review-guardrail", "ruff", "bandit")
        ),
        "Deterministic checks": ", ".join(check.name for check in DEFAULT_CHECKS),
        "Timing": "Wall time from patch/source loading through review, excluding checkout setup and scoring. Cases run serially.",
        "Findings included": "All severities",
        "Model prompt": f"{model_review.PROMPT_ID} {model_review.PROMPT_VERSION}",
        "Model selection": f"Flagged Python hunks or at least {model_review.COMPLEXITY_LINES} added code lines",
        "Request limits": f"{model_review.MAX_INPUT_BYTES} input bytes, {codex_cli.MAX_REPLY_BYTES} maximum reply bytes, {model_review.TIMEOUT_SECONDS}s timeout, one CLI invocation",
        "Provider / requested model": f"Codex CLI / {codex_cli.MODEL} ({codex_cli.REASONING} reasoning)"
        if live
        else "Awaiting live run",
    }
    run = Run(
        settings=settings,
        rates=rates,
        deterministic=[],
        model_assisted=[] if live else None,
    )
    for patch in patches:
        with TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "checkout"
            try:
                shutil.copytree(
                    patch.parents[1] / "base",
                    checkout,
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
                subprocess.run(
                    ["git", "init", "-q"], cwd=checkout, check=True, capture_output=True
                )
                subprocess.run(
                    ["git", "apply", patch],
                    cwd=checkout,
                    check=True,
                    capture_output=True,
                )
                # Include fixture config files even when the user's Git ignores *.env.
                subprocess.run(
                    ["git", "-c", "core.autocrlf=false", "add", "--force", "."],
                    cwd=checkout,
                    check=True,
                    capture_output=True,
                )
            except (OSError, subprocess.CalledProcessError):
                failure = Outcome(
                    id=patch.stem, incomplete="Could not prepare the patched checkout."
                )
                run.deterministic.append(failure)
                if live:
                    run.model_assisted.append(failure)
                continue
            run.deterministic.append(review_case(patch, checkout, False, provider))
            if run.model_assisted is not None:
                run.model_assisted.append(review_case(patch, checkout, True, provider))
    return run


@app.command("run")
def run_benchmark(
    out: Annotated[
        Path, typer.Option(help="Save per-case review observations as JSON.")
    ],
    dataset: Path = Path("benchmarks"),
    live: Annotated[
        bool, typer.Option("--live", help="Also run the model-assisted reviewer.")
    ] = False,
    input_rate: Annotated[
        float | None, typer.Option(min=0, help="USD per million uncached input tokens.")
    ] = None,
    cached_input_rate: Annotated[
        float | None, typer.Option(min=0, help="USD per million cached input tokens.")
    ] = None,
    output_rate: Annotated[
        float | None, typer.Option(min=0, help="USD per million output tokens.")
    ] = None,
) -> None:
    try:
        rates = Rates(
            input=input_rate, cached_input=cached_input_rate, output=output_rate
        )
        run = measure(dataset, live, rates)
        write_output(out, run.model_dump_json(indent=2) + "\n")
    except (ValueError, OSError, ModuleNotFoundError) as exc:
        typer.echo(f"Benchmark error: {exc}", err=True)
        raise typer.Exit(3) from exc
    typer.echo(f"Saved {len(run.deterministic)} cases to {out}")
    if any(row.incomplete for row in [*run.deterministic, *(run.model_assisted or [])]):
        raise typer.Exit(3)


@app.command("score")
def score_benchmark(
    outcomes: Path,
    labels: Path = Path("benchmarks/labels.yaml"),
    out: Annotated[
        Path | None, typer.Option(help="Write a Markdown report. Default: stdout.")
    ] = None,
) -> None:
    try:
        run = Run.model_validate_json(outcomes.read_bytes())
        records = yaml.safe_load(labels.read_text(encoding="utf-8"))
        targets = {r["id"]: r["target"] for r in records}
        if len(records) != len(targets):
            raise ValueError("Duplicate case ID in scoring key.")
        report = summary(run, targets)
        if out:
            write_output(out, report)
        else:
            sys.stdout.buffer.write(report.encode("utf-8"))
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        typer.echo(f"Benchmark scoring error: {exc}", err=True)
        raise typer.Exit(3) from exc
