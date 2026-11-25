import os
import subprocess
import sysconfig
from importlib.metadata import version
from pathlib import Path

import pytest


@pytest.mark.parametrize("option", ["--help", "--version"])
def test_installed_cli(option, tmp_path):
    scripts = Path(sysconfig.get_path("scripts"))
    command = scripts / ("guardrail.exe" if os.name == "nt" else "guardrail")
    result = subprocess.run(
        [command, option], cwd=tmp_path, capture_output=True, text=True, check=True
    )

    if option == "--version":
        assert result.stdout.strip() == version("ai-code-review-guardrail")
    else:
        assert "Review Python changes" in result.stdout
