"""Composite action entry point. The PR is fetched as objects, never checked out."""

import base64
import json
import os
import secrets
import subprocess
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory

from guardrail.cli import app


def enabled(name: str) -> bool:
    value = os.environ[name].lower()
    if value not in {"true", "false"}:
        raise SystemExit(f"{name} must be true or false.")
    return value == "true"


def main() -> int:
    if os.environ["GITHUB_EVENT_NAME"] != "pull_request":
        raise SystemExit("Guardrail requires a pull_request event.")
    event = json.loads(
        Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")
    )
    pr = event["pull_request"]
    base, head = pr["base"]["sha"], pr["head"]["sha"]
    repo = os.environ["GITHUB_REPOSITORY"]
    use_llm = enabled("USE_LLM")
    if use_llm and pr["head"]["repo"]["full_name"] != repo:
        print("Model review disabled for a fork pull request.", flush=True)
        use_llm = False
    args = [
        "review",
        "--base",
        base,
        "--head",
        head,
        "--fail-on",
        os.environ["FAIL_ON"],
        "--use-llm" if use_llm else "--no-llm",
    ]
    if enabled("COMMENT"):
        args.extend(["--comment-on-pr", str(event["number"]), "--repository", repo])

    origin = f"{os.environ['GITHUB_SERVER_URL']}/{repo}.git"
    auth = base64.b64encode(
        f"x-access-token:{os.environ['GITHUB_TOKEN']}".encode()
    ).decode()
    with TemporaryDirectory(dir=os.environ["RUNNER_TEMP"]) as work, chdir(work):
        subprocess.run(["git", "init", "--bare", "--quiet"], check=True)
        # Fetch both immutable event SHAs and their ancestors from the base repo.
        # The PR head is reachable there even when the source branch is in a fork.
        subprocess.run(
            [
                "git",
                "--config-env=http.extraHeader=GUARDRAIL_AUTH",
                "fetch",
                "--quiet",
                "--no-tags",
                "--no-recurse-submodules",
                origin,
                base,
                head,
            ],
            env={**os.environ, "GUARDRAIL_AUTH": f"Authorization: Basic {auth}"},
            check=True,
        )
        # Filenames and findings may contain text resembling runner commands.
        marker = secrets.token_hex(32)
        print(f"::stop-commands::{marker}", flush=True)
        try:
            status = app(args=args, standalone_mode=False)
        finally:
            print(f"::{marker}::", flush=True)
    if status == 1:
        print("::warning::Guardrail found warnings below the failure threshold.")
        return 0
    return status


if __name__ == "__main__":
    raise SystemExit(main())
