"""Publish the completed review as a GitHub Actions bot conversation comment."""

import os
from html import escape

from github import Auth, Github, GithubException, RateLimitExceededException
from requests.exceptions import RequestException

from guardrail.review import DEFAULT_CHECKS, Finding
from guardrail.source import ReviewSource

MARKER = "<!-- guardrail:pr-summary:v1 -->"
BOT = "github-actions[bot]"


class PublishError(Exception):
    """A safe diagnostic for an operational publication failure."""


def render_summary(
    source: ReviewSource, findings: list[Finding], model_status: str | None
) -> str:
    def literal(value: str) -> str:
        # Render paths and model text literally inside HTML code/pre elements.
        text = (
            source.redactor.redact(value).encode("utf-8", "backslashreplace").decode()
        )
        return escape(text[:1000] + ("…" if len(text) > 1000 else ""))

    lines = [MARKER, "## Guardrail review", ""]
    if source.skipped_reason is None:
        lines.append("Skipped checks: none.")
    else:
        names = ", ".join(c.name for c in DEFAULT_CHECKS if c.requires_whole_file)
        lines.append(
            f"Skipped checks: {names}.\n<pre>{literal(source.skipped_reason)}</pre>"
        )
    lines.extend(
        ["", f"<pre>{literal(model_status or 'Model review disabled.')}</pre>", ""]
    )
    if not findings:
        lines.append("No findings.")
        return "\n".join(lines)

    lines.append(f"### Findings ({len(findings)})")
    size = len("\n".join(lines).encode("utf-8"))
    for i, finding in enumerate(findings):
        location = literal(finding.file)
        if finding.line is not None:
            location += f":{finding.line}"
        suggestion = finding.suggestion or "Review the reported change."
        entry = (
            f"\n**{finding.severity.value.upper()}**\n\n"
            f"<pre>{location}  {literal(finding.check)}\n"
            f"{literal(finding.message)}\nSuggested action: {literal(suggestion)}</pre>"
        )
        # Leave room under GitHub's comment limit for the omission notice.
        size += len(entry.encode("utf-8")) + 1
        if size > 60_000:
            lines.append(
                f"\n{len(findings) - i} more finding(s). See the full review in the job output."
            )
            break
        lines.append(entry)
    return "\n".join(lines)


def publish_summary(repository: str, number: int, body: str) -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise PublishError("Set GITHUB_TOKEN to the workflow's GitHub Actions token.")
    try:
        with Github(
            auth=Auth.Token(token),
            base_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            timeout=15,
            retry=0,
        ) as client:
            pr = client.get_repo(repository).get_pull(number)
            for comment in pr.get_issue_comments():
                if (
                    comment.user is not None
                    and comment.user.login == BOT
                    and comment.user.type == "Bot"
                    and (comment.body or "").partition("\n")[0].rstrip("\r") == MARKER
                ):
                    if comment.body != body:
                        comment.edit(body)
                    return "PR summary is up to date."
            pr.create_issue_comment(body)
            return "Created PR summary."
    except RateLimitExceededException:
        raise PublishError("GitHub API rate limit exceeded.") from None
    except GithubException as exc:
        if exc.status == 403:
            return "PR summary could not be published (HTTP 403). Check pull-requests: write permission."
        raise PublishError(f"GitHub returned HTTP {exc.status}.") from None
    except RequestException:
        raise PublishError("Could not reach the GitHub API.") from None
