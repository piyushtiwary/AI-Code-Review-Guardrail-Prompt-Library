import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_pr.py"


def test_action_on_sample_pull_request(tmp_path):
    repo = tmp_path / "owner" / "project.git"
    repo.mkdir(parents=True)

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(name):
        git("add", ".")
        git("commit", "-qm", name)
        return git("rev-parse", "HEAD")

    git("init", "-b", "base")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    (repo / "common.txt").write_text("ancestor")
    commit("Common ancestor")
    git("branch", "feature")
    (repo / "base-only.txt").write_text("base")
    base = commit("Advance the base independently")
    git("switch", "feature")
    # A review must read this module as data.
    (repo / "setup.py").write_text("raise RuntimeError('reviewed module executed')\n")
    warning = commit("Logic without tests")
    value = "ghp_" + "a" * 36
    (repo / "key.txt").write_text(value + "\n")
    blocking = commit("Add a synthetic credential")
    (repo / "setup.py").unlink()
    (repo / "key.txt").unlink()
    (repo / "readme.txt").write_text("documentation")
    clean = commit("Keep documentation only")

    event_path = tmp_path / "event.json"
    env = {
        **os.environ,
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_SERVER_URL": tmp_path.as_uri(),
        "GITHUB_REPOSITORY": "owner/project",
        "GITHUB_TOKEN": "fixture",
        "RUNNER_TEMP": str(tmp_path),
        "USE_LLM": "true",
        "COMMENT": "false",
    }
    env.pop("OPENAI_API_KEY", None)

    def review(head, threshold):
        event_path.write_text(
            json.dumps(
                {
                    "number": 12,
                    "pull_request": {
                        "base": {"sha": base},
                        "head": {"sha": head, "repo": {"full_name": "fork/project"}},
                    },
                }
            )
        )
        return subprocess.run(
            [sys.executable, "-I", SCRIPT],
            cwd=tmp_path,
            env={**env, "FAIL_ON": threshold},
            check=False,
            capture_output=True,
            text=True,
        )

    for head, threshold, expected in (
        (warning, "blocking", 0),
        (warning, "warning", 2),
        (blocking, "blocking", 2),
        (blocking, "warning", 2),
        (clean, "warning", 0),
    ):
        result = review(head, threshold)
        assert result.returncode == expected, result.stderr or result.stdout
        assert "Model review disabled for a fork" in result.stdout
        assert value not in result.stdout + result.stderr
        if head == warning and threshold == "blocking":
            assert "::warning::" in result.stdout
        if head == clean:
            assert (
                "Changed files: 1" in result.stdout and "No findings." in result.stdout
            )
    assert review("0" * 40, "blocking").returncode != 0

    # A fork's read-only workflow token can read the PR but cannot post.
    requests = []

    class ReadOnlyAPI(BaseHTTPRequestHandler):
        def reply(self, status, data):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_GET(self):
            root = f"{env['GITHUB_API_URL']}/repos/owner/project"
            routes = {
                "/repos/owner/project": {"url": root},
                "/repos/owner/project/pulls/12": {"issue_url": f"{root}/issues/12"},
                "/repos/owner/project/issues/12/comments": [],
            }
            self.reply(200, routes[self.path])

        def do_POST(self):
            requests.append(self.path)
            self.reply(403, {"message": "Resource not accessible by integration"})

    with HTTPServer(("127.0.0.1", 0), ReadOnlyAPI) as api:
        env.update(GITHUB_API_URL=f"http://127.0.0.1:{api.server_port}", COMMENT="true")
        thread = Thread(target=api.serve_forever)
        thread.start()
        try:
            result = review(warning, "blocking")
        finally:
            api.shutdown()
            thread.join()
    assert requests == ["/repos/owner/project/issues/12/comments"]
    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stdout and "HTTP 403" in result.stderr
