"""Build a wheel, then exercise its CLI and resources outside the checkout."""

import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="guardrail wheel ") as directory:
        work = Path(directory)
        subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", work, ROOT],
            cwd=work,
            check=True,
        )
        (wheel,) = work.glob("*.whl")
        env = work / "venv"
        venv.create(env, with_pip=True)
        scripts = env / ("Scripts" if os.name == "nt" else "bin")
        extension = ".exe" if os.name == "nt" else ""
        python = scripts / f"python{extension}"
        cli = scripts / f"guardrail{extension}"
        clean_env = os.environ.copy()
        for name in ("PYTHONPATH", "PYTHONHOME", "OPENAI_API_KEY", "GITHUB_TOKEN"):
            clean_env.pop(name, None)

        def run(*args, capture=False):
            return subprocess.run(
                args,
                cwd=work,
                env=clean_env,
                stdout=subprocess.PIPE if capture else None,
                check=True,
            ).stdout

        run(python, "-I", "-m", "pip", "install", wheel)
        run(cli, "--version")
        run(cli, "--help")
        records = json.loads(run(cli, "prompts", "eval", "--offline", capture=True))
        examples = {"last-seat", "lookup", "booking-capacity"}
        if {r["example"] for r in records if r["passed"]} != examples:
            raise RuntimeError("Installed wheel did not pass all three prompt examples")
        out = work / "generated tests"
        run(cli, "storygen", ROOT / "stories/reroute.feature", "--out", out)
        scaffold = (out / "test_reroute.py").read_text(encoding="utf-8")
        compile(scaffold, "test_reroute.py", "exec")
        if scaffold.count('pytest.fail("TODO: implement this scenario")') != 2:
            raise RuntimeError("Installed template did not generate both TODOs")
        print("Wheel smoke passed: CLI, three prompt examples and two scaffold TODOs.")


if __name__ == "__main__":
    main()
