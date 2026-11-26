import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from guardrail.source import patch_source

ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
PATCHES = sorted((ROOT / "inputs").glob("*/patches/*.patch"))
LABELS = yaml.safe_load((ROOT / "labels.yaml").read_text(encoding="utf-8"))
TARGETS = {case["id"]: case["target"] for case in LABELS}


def test_dataset_inventory():
    ids = [patch.stem for patch in PATCHES]
    assert len(set(ids)) == len(ids) == len(TARGETS) == len(LABELS)
    assert set(ids) == TARGETS.keys()
    positives = [target for target in TARGETS.values() if target is not None]
    assert 25 <= len(positives) <= 30
    assert len(ids) - len(positives) == 10
    assert {target["category"] for target in positives} == {
        "imports",
        "test-reference",
        "exception-handling",
        "secrets",
        "off-by-one",
        "missing-none-handling",
        "no-test-changes",
    }


@pytest.mark.parametrize("patch", PATCHES, ids=lambda path: path.stem)
def test_case_applies_and_labels_added_lines(patch, tmp_path):
    checkout = shutil.copytree(patch.parents[1] / "base", tmp_path / "checkout")
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(["git", "apply", patch], cwd=checkout, check=True)

    source = patch_source(patch, checkout)
    assert source.skipped_reason is None, source.skipped_reason
    assert source.changes
    for path, data in source.python_files.items():
        compile(data, path, "exec")

    added = {
        (change.new_path, line.target_line_no)
        for change in source.changes
        for line in change.added_lines
    }
    target = TARGETS[patch.stem]
    if target is not None:
        anchors = [(anchor["file"], anchor["line"]) for anchor in target["anchors"]]
        assert anchors
        assert len(set(anchors)) == len(anchors)
        assert set(anchors) <= added
