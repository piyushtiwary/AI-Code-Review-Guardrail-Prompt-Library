from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from github import GithubException
from typer.testing import CliRunner

from guardrail import cli, pr_summary
from guardrail.review import Finding
from guardrail.source import ReviewSource


def comment(login, body, kind="Bot"):
    item = Mock(body=body, user=SimpleNamespace(login=login, type=kind))
    item.edit.side_effect = lambda body: setattr(item, "body", body)
    return item


@pytest.fixture
def publish(tmp_path, monkeypatch):
    # Exercise the normal CLI entry point, replacing only GitHub's client.
    client = MagicMock()
    monkeypatch.setattr(pr_summary, "Github", client)
    monkeypatch.setenv("GITHUB_TOKEN", "workflow-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/project")
    pr = client.return_value.__enter__.return_value.get_repo.return_value.get_pull.return_value
    comments = []
    pr.get_issue_comments.side_effect = lambda: iter(comments)
    pr.create_issue_comment.side_effect = lambda body: comments.append(
        comment(pr_summary.BOT, body)
    )
    patch = tmp_path / "change.patch"

    def run(text, *options):
        patch.write_text(text, encoding="utf-8")
        return CliRunner().invoke(
            cli.app, ["review", "--diff", str(patch), "--patch-only", *options]
        )

    return run, comments, pr


def test_summary_lifecycle(publish):
    run, comments, pr = publish
    foreign = [
        comment("contributor", pr_summary.MARKER + "\nquoted", "User"),
        comment("another-app[bot]", pr_summary.MARKER + "\nother tool"),
        comment(pr_summary.BOT, "unrelated workflow comment"),
    ]
    comments.extend(foreign)
    token = "ghp_" + "Z" * 36
    change = f"--- /dev/null\n+++ b/settings.txt\n@@ -0,0 +1 @@\n+token={token}\n"
    assert run(change).exit_code == 2
    pr.get_issue_comments.assert_not_called()

    result = run(change, "--comment-on-pr", "12")
    assert result.exit_code == 2, result.output
    summary = comments[-1]
    assert summary.body.startswith(pr_summary.MARKER + "\n")
    for expected in (
        "BLOCKING",
        "settings.txt:1",
        "rotate",
        "Skipped checks: imports",
        "Model review disabled",
    ):
        assert expected in summary.body
    assert token not in summary.body + result.output

    result = run(change.replace("settings.txt", "other.txt"), "--comment-on-pr", "12")
    assert result.exit_code == 2
    assert "other.txt:1" in summary.body and "settings.txt" not in summary.body
    clean = run("", "--comment-on-pr", "12", "--use-llm")
    assert clean.exit_code == 0
    assert "No findings." in summary.body
    assert "Model review skipped: no eligible Python hunks." in summary.body
    assert "BLOCKING" not in summary.body and "other.txt" not in summary.body
    assert len(comments) == 4 and summary.edit.call_count == 2
    for item in foreign:
        item.edit.assert_not_called()


@pytest.mark.parametrize(("http", "status"), [(403, 1), (503, 3)])
def test_publish_failure_preserves_report(publish, http, status):
    run, _, pr = publish
    pr.create_issue_comment.side_effect = GithubException(http, "raw response", {})
    result = run(
        "--- /dev/null\n+++ b/a.py\n@@ -0,0 +1 @@\n+x = 1\n", "--comment-on-pr", "12"
    )
    assert result.exit_code == status, result.output
    assert "WARNING" in result.stdout
    assert f"HTTP {http}" in result.stderr
    assert "raw response" not in result.output


def test_large_summary_escapes_untrusted_content_and_keeps_status():
    finding = Finding(
        check="example",
        file="file<1>.py",
        line=1,
        severity="warning",
        message="</pre><img src=x>" * 50,
    )
    body = pr_summary.render_summary(
        ReviewSource((), {}), [finding] * 200, "Model review incomplete."
    )
    assert "file&lt;1&gt;.py:1" in body and "<img" not in body
    assert "Model review incomplete." in body and "more finding(s)" in body
    assert len(body.encode()) < 65_536
