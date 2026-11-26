"""Score saved review outputs against case labels without calling the reviewer."""

from collections import Counter
from statistics import mean
from typing import Annotated, Literal

from pydantic import BaseModel, Field, FiniteFloat, NonNegativeFloat

from guardrail.codex_cli import ResponseUsage
from guardrail.model_review import PROMPT_ID
from guardrail.review import Finding

Category = Literal[
    "imports",
    "test-reference",
    "exception-handling",
    "secrets",
    "off-by-one",
    "missing-none-handling",
    "no-test-changes",
    "other",
]
Rate = Annotated[FiniteFloat, Field(ge=0)]


class Rates(BaseModel):
    """User-supplied USD per million tokens for the selected model."""

    input: Rate | None = None
    cached_input: Rate | None = None
    output: Rate | None = None


class Observation(Finding):
    # Version 1.0.0 returns prose. A grader can categorize it before opening the key.
    category: Category | None = None

    @property
    def scoring_category(self) -> str | None:
        return self.category if self.check == PROMPT_ID else self.check


class Outcome(BaseModel):
    id: str
    elapsed_seconds: NonNegativeFloat | None = None
    findings: list[Observation] = Field(default_factory=list)
    incomplete: str | None = None
    model_status: str | None = None
    requested: bool = False
    usage: ResponseUsage | None = None

    def cost(self, rates: Rates) -> float | None:
        if not self.requested:
            return 0.0
        usage = self.usage
        if usage is None or usage.input_tokens is None or usage.output_tokens is None:
            return None
        cached = usage.cached_input_tokens
        if cached is None:
            if usage.input_tokens and rates.input != rates.cached_input:
                return None
            cached = 0
        if not 0 <= cached <= usage.input_tokens or usage.output_tokens < 0:
            return None
        total = 0.0
        for count, rate in (
            (usage.input_tokens - cached, rates.input),
            (cached, rates.cached_input),
            (usage.output_tokens, rates.output),
        ):
            if count:
                if rate is None:
                    return None
                total += count * rate
        return total / 1_000_000


class Run(BaseModel):
    settings: dict
    rates: Rates
    deterministic: list[Outcome]
    model_assisted: list[Outcome] | None = None


def verdict(row: Outcome, target: dict | None) -> str:
    if target is None:
        return "False alarm" if row.findings else "No finding"
    anchors = {(a["file"], a["line"]) for a in target["anchors"]}
    if any(
        f.scoring_category == target["category"] and (f.file, f.line) in anchors
        for f in row.findings
    ):
        return "Caught"
    if row.incomplete or any(f.scoring_category is None for f in row.findings):
        return "No confirmed hit"
    return "Missed"


def score(rows: list[Outcome], targets: dict, rates: Rates) -> dict:
    ids = [row.id for row in rows]
    if len(set(ids)) != len(ids) or set(ids) != targets.keys():
        raise ValueError("Run must contain exactly one outcome for every labeled case.")
    counts = Counter(verdict(row, targets[row.id]) for row in rows)
    positives = sum(t is not None for t in targets.values())
    costs = [row.cost(rates) for row in rows]
    times = [row.elapsed_seconds for row in rows]
    return {
        "caught": counts["Caught"],
        "targets": positives,
        "recall": counts["Caught"] / positives if positives else None,
        "false_alarms": counts["False alarm"],
        "clean": len(rows) - positives,
        "mean_seconds": mean(times) if rows and None not in times else None,
        "mean_usd": mean(costs) if rows and None not in costs else None,
        "unknown_costs": costs.count(None),
        "incomplete": sum(row.incomplete is not None for row in rows),
        "unclassified": sum(
            f.scoring_category is None for row in rows for f in row.findings
        ),
    }


def summary(run: Run, targets: dict) -> str:
    settings = run.settings
    lines = [
        "# Review benchmark results",
        "",
        "| Mode | Caught targets | Recall | Clean PRs with a false alarm | Mean seconds/PR | Mean model USD/PR | Incomplete PRs |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    notes = []
    for mode, rows in (
        ("Deterministic", run.deterministic),
        ("Model-assisted", run.model_assisted),
    ):
        if rows is None:
            lines.append(f"| {mode} (awaiting live run) | — | — | — | — | — | — |")
            continue
        s = score(rows, targets, run.rates)
        recall = f"{s['recall']:.1%}" if s["recall"] is not None else "—"
        seconds = (
            f"{s['mean_seconds']:.3f}"
            if s["mean_seconds"] is not None
            else "Unavailable"
        )
        dollars = f"{s['mean_usd']:.8f}" if s["mean_usd"] is not None else "Unavailable"
        partial = " (partial)" if s["incomplete"] or s["unclassified"] else ""
        lines.append(
            f"| {mode}{partial} | {s['caught']}/{s['targets']} | {recall} | "
            f"{s['false_alarms']}/{s['clean']} | {seconds} | {dollars} | {s['incomplete']} |"
        )
        if s["unclassified"]:
            notes.append(
                f"{mode} has {s['unclassified']} finding(s) awaiting category assignment. Recall counts confirmed matches."
            )
        if s["unknown_costs"]:
            notes.append(
                f"{mode} cost is unavailable for {s['unknown_costs']} PR(s) with missing usage or rates."
            )
        if s["incomplete"]:
            notes.append(
                f"{mode} counts observed findings on incomplete PRs in the full dataset denominators."
            )
    if notes:
        lines.extend(["", *notes])
    lines.extend(["", "## Run settings", ""])
    lines.extend(f"- {name}: {value}" for name, value in settings.items())
    if run.model_assisted is not None:
        rows = run.model_assisted
        models = Counter(r.usage.model for r in rows if r.usage and r.usage.model)
        versions = ", ".join(f"{model} ({n})" for model, n in sorted(models.items()))
        lines.append(
            f"- Provider requests: {sum(r.requested for r in rows)}. Returned model versions: {versions or 'unavailable'}."
        )
        lines.append(
            "- Rates in USD per million tokens: "
            + ", ".join(
                f"{name} {rate if rate is not None else 'unavailable'}"
                for name, rate in run.rates.model_dump().items()
            )
            + "."
        )
    lines.extend(["", "## Case outcomes", ""])
    columns = ["Case", "Target", "Deterministic"]
    groups = [run.deterministic]
    if run.model_assisted is not None:
        columns.extend(["Model-assisted", "Model USD"])
        groups.append(run.model_assisted)
    lines.extend(["| " + " | ".join(columns) + " |", "|" + " --- |" * len(columns)])
    by_id = [{r.id: r for r in group} for group in groups]
    for case_id, target in sorted(targets.items()):
        cells = [case_id, target["category"] if target else "Clean"]
        for group in by_id:
            row = group[case_id]
            result = verdict(row, target)
            cells.append(f"{result} (incomplete)" if row.incomplete else result)
        if run.model_assisted is not None:
            cost = by_id[1][case_id].cost(run.rates)
            cells.append(f"{cost:.8f}" if cost is not None else "Unavailable")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
