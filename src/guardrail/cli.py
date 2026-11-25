from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from guardrail.patch import PatchInputError
from guardrail.report import render_report
from guardrail.review import (
    DEFAULT_CHECKS,
    CheckError,
    FailOn,
    review_status,
    run_checks,
)
from guardrail.source import SourceError, patch_source, revision_source

app = typer.Typer(add_completion=False)


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
    fail_on: Annotated[
        FailOn, typer.Option(help="Minimum severity that returns exit status 2.")
    ] = FailOn.BLOCKING,
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
        typer.echo(f"Review input error: {exc}", err=True)
        raise typer.Exit(3) from exc

    try:
        findings = run_checks(source, DEFAULT_CHECKS)
    except CheckError as exc:
        typer.echo(f"Review check error: {exc}", err=True)
        raise typer.Exit(3) from exc

    render_report(Console(), source, findings)
    raise typer.Exit(review_status(findings, fail_on))
