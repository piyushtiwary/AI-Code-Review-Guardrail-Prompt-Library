import asyncio
import json
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from guardrail.benchmark import app as benchmark_app
from guardrail.model_review import openai_review, review_with_model
from guardrail.patch import PatchInputError
from guardrail.pr_summary import PublishError, publish_summary, render_summary
from guardrail.prompt_eval import evaluate
from guardrail.prompts import PromptError, load_prompts
from guardrail.report import render_report
from guardrail.review import (
    DEFAULT_CHECKS,
    CheckError,
    FailOn,
    review_status,
    run_checks,
    sorted_findings,
)
from guardrail.scaffold import write_scaffold
from guardrail.secrets import SecretRedactor
from guardrail.source import SourceError, patch_source, revision_source
from guardrail.story import StoryInputError, parse_story

app = typer.Typer(add_completion=False)
prompts_app = typer.Typer(help="Work with versioned prompts.")
app.add_typer(prompts_app, name="prompts")
app.add_typer(benchmark_app, name="benchmark")


@prompts_app.command("eval")
def eval_prompts(
    live: Annotated[
        bool,
        typer.Option(
            "--live/--offline", help="Call the model or replay fixture outputs."
        ),
    ] = False,
) -> None:
    """Score all examples and print JSON. The default replays offline fixtures."""
    try:
        records, status = asyncio.run(
            evaluate(load_prompts().values(), openai_review if live else None)
        )
        typer.echo(
            json.dumps(
                [row.model_dump(mode="json", exclude_none=True) for row in records],
                indent=2,
            )
        )
    except (OSError, PromptError) as exc:
        typer.echo(f"Prompt error: {exc}", err=True)
        raise typer.Exit(3) from exc
    raise typer.Exit(status)


@prompts_app.command("render")
def render_prompt(prompt_id: str, version: str, example: str) -> None:
    """Render an example from an exact prompt version to standard output."""
    try:
        entry = load_prompts().get((prompt_id, version))
        if entry is None:
            raise PromptError(f"unknown prompt version: {prompt_id} {version}")
        if example not in entry.fixtures:
            raise PromptError(f"unknown example for {prompt_id} {version}: {example}")
        typer.echo(entry.prompt.render(entry.fixtures[example].inputs), nl=False)
    except (OSError, PromptError) as exc:
        typer.echo(f"Prompt error: {exc}", err=True)
        raise typer.Exit(3) from exc


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    show_version: Annotated[
        bool, typer.Option("--version", help="Show the installed version and exit.")
    ] = False,
) -> None:
    """Review Python changes and work with versioned prompts."""
    if show_version:
        typer.echo(version("ai-code-review-guardrail"))
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command()
def review(
    base: Annotated[str | None, typer.Option(help="Base Git revision.")] = None,
    head: Annotated[str | None, typer.Option(help="Head Git revision.")] = None,
    diff: Annotated[
        Path | None, typer.Option(help="Path to a saved unified patch.")
    ] = None,
    source_root: Annotated[
        Path | None,
        typer.Option(
            help="Post-change Git checkout for a saved patch (default: current checkout)."
        ),
    ] = None,
    patch_only: Annotated[
        bool, typer.Option(help="Review the patch without a checkout.")
    ] = False,
    use_llm: Annotated[
        bool,
        typer.Option(
            "--use-llm", help="Request optional model review after local checks."
        ),
    ] = False,
    no_llm: Annotated[
        bool,
        typer.Option("--no-llm", help="Run only local checks, overriding --use-llm."),
    ] = False,
    fail_on: Annotated[
        FailOn, typer.Option(help="Minimum severity that returns exit status 2.")
    ] = FailOn.BLOCKING,
    comment_on_pr: Annotated[
        int | None,
        typer.Option(min=1, help="Publish or update the summary for this PR number."),
    ] = None,
    repository: Annotated[
        str | None,
        typer.Option(envvar="GITHUB_REPOSITORY", help="GitHub owner/repo for the PR."),
    ] = None,
) -> None:
    """Run review checks on a Git comparison or a saved patch."""
    try:
        if diff is None:
            if base is None or head is None or patch_only or source_root is not None:
                raise typer.BadParameter(
                    "Use --base and --head together, or supply --diff."
                )
            source = revision_source(Path.cwd(), base, head)
        else:
            if base is not None or head is not None:
                raise typer.BadParameter(
                    "--diff cannot be combined with --base or --head."
                )
            if patch_only and source_root is not None:
                raise typer.BadParameter("Choose either --source-root or --patch-only.")
            source = patch_source(
                diff, None if patch_only else source_root or Path.cwd()
            )
    except (PatchInputError, SourceError) as exc:
        typer.echo(f"Review input error: {SecretRedactor().redact(str(exc))}", err=True)
        raise typer.Exit(3) from exc

    try:
        findings = run_checks(source, DEFAULT_CHECKS)
    except CheckError as exc:
        typer.echo(f"Review check error: {exc}", err=True)
        raise typer.Exit(3) from exc

    model_status = None
    if use_llm and not no_llm:
        model_review = asyncio.run(review_with_model(source, findings, openai_review))
        findings = sorted_findings([*findings, *model_review.findings])
        model_status = model_review.status
    render_report(Console(), source, findings, model_status)
    if comment_on_pr is not None:
        if not repository:
            typer.echo(
                "PR summary error: supply --repository or GITHUB_REPOSITORY.", err=True
            )
            raise typer.Exit(3)
        try:
            message = publish_summary(
                repository,
                comment_on_pr,
                render_summary(source, findings, model_status),
            )
        except PublishError as exc:
            typer.echo(f"PR summary error: {exc}", err=True)
            raise typer.Exit(3) from exc
        typer.echo(message, err=True)
    raise typer.Exit(review_status(findings, fail_on))


@app.command()
def storygen(
    story: Annotated[Path, typer.Argument(help="Acceptance criteria file.")],
    out: Annotated[
        Path, typer.Option(help="Directory for the generated pytest module.")
    ],
) -> None:
    """Generate a failing pytest scaffold for each scenario in a story."""
    try:
        parsed = parse_story(story.read_text(encoding="utf-8-sig"))
        target = write_scaffold(parsed, story.stem, out)
    except FileExistsError as exc:
        typer.echo(
            f"Path already exists: {exc.filename}. Choose another --out directory.",
            err=True,
        )
        raise typer.Exit(3) from exc
    except (OSError, UnicodeError, StoryInputError) as exc:
        typer.echo(f"Story generation error for {story}: {exc}", err=True)
        raise typer.Exit(3) from exc
    typer.echo(f"Created {target}")
