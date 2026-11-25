import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "offline"


@pytest.mark.parametrize(("case", "status"), [("clean", 0), ("defective", 1)])
def test_offline_review_example(case, status, tmp_path):
    checkout = shutil.copytree(EXAMPLES / "checkout", tmp_path / case)
    fixture = EXAMPLES / case
    shutil.copyfile(fixture / "changes.patch", checkout / "changes.patch")
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(["git", "apply", "changes.patch"], cwd=checkout, check=True)

    scripts = Path(sysconfig.get_path("scripts"))
    command = scripts / ("guardrail.exe" if os.name == "nt" else "guardrail")
    result = subprocess.run(
        [command, "review", "--diff", "changes.patch", "--no-llm"],
        cwd=checkout,
        env={**os.environ, "NO_COLOR": "1", "COLUMNS": "80"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == status, result.stderr or result.stdout
    assert result.stderr == ""
    assert result.stdout == (fixture / "expected.txt").read_text(encoding="utf-8")
