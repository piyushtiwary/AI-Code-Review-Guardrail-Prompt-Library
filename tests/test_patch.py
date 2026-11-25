from pathlib import Path

import pytest

from guardrail.patch import PatchInputError, parse_patch, read_patch

FIXTURES = Path(__file__).parent / "fixtures" / "patches"


def test_added_and_deleted_files_keep_the_existing_side():
    added, deleted = read_patch(FIXTURES / "add-delete.patch")
    assert (added.status, added.old_path, added.new_path) == (
        "added",
        None,
        "src/new.py",
    )
    assert [line.target_line_no for line in added.added_lines] == [1, 2]
    assert (deleted.status, deleted.old_path, deleted.new_path) == (
        "deleted",
        "src/obsolete.py",
        None,
    )
    assert list(deleted.added_lines) == []


def test_rename_without_hunks():
    (change,) = read_patch(FIXTURES / "rename.patch")
    assert (change.old_path, change.new_path) == ("src/old name.py", "src/new name.py")
    assert change.status == "renamed"
    assert change.hunks == ()
    assert not change.is_binary


def test_binary_change():
    (change,) = read_patch(FIXTURES / "binary.patch")
    assert change.old_path == change.new_path == "assets/logo.png"
    assert change.status == "modified"
    assert change.is_binary
    assert change.hunks == ()


def test_each_hunk_uses_its_own_target_coordinates():
    (change,) = read_patch(FIXTURES / "two-hunks.patch")
    assert change.old_path == change.new_path == "src/settings.py"
    assert change.status == "modified"
    assert [
        (h.source_start, h.source_length, h.target_start, h.target_length)
        for h in change.hunks
    ] == [(2, 3, 2, 4), (18, 3, 19, 2)]
    assert [
        (line.target_line_no, line.value.rstrip("\n")) for line in change.added_lines
    ] == [(3, "second = 20"), (4, "extra = 3"), (20, "active = True")]


def test_empty_patch():
    assert parse_patch("") == ()


def test_empty_file_creation_is_a_change():
    text = "diff --git a/empty.py b/empty.py\nnew file mode 100644\n"
    (change,) = parse_patch(text + "index 0000000..e69de29\n")
    assert (change.status, change.new_path, change.hunks) == ("added", "empty.py", ())
    with pytest.raises(PatchInputError):
        parse_patch(text + "index 0000000..1234567\n")


def test_plain_paths_and_header_shaped_lines_in_a_hunk():
    text = "--- a/file\n+++ a/file\n@@ -1 +1 @@\n--- old\n+++ new\n"
    (change,) = parse_patch(text.replace("\n", "\r\n"))
    assert change.old_path == change.new_path == "a/file"
    assert [(line.target_line_no, line.value) for line in change.added_lines] == [
        (1, "++ new\n")
    ]


def test_truncated_patch_is_an_input_error():
    with pytest.raises(PatchInputError, match="truncated.patch.*shorter than expected"):
        read_patch(FIXTURES / "truncated.patch")


@pytest.mark.parametrize(
    "text",
    [
        "this is not a patch\n",
        "diff --git a/f b/f\n",
        "diff --git a/f b/g\nrename from f\n",
        "--- a/f\n+++ b/f\n",
        "--- a/f\n+++ b/f\n@@ broken range @@\n+line\n",
    ],
)
def test_incomplete_file_is_an_input_error(text):
    with pytest.raises(PatchInputError):
        parse_patch(text)


@pytest.mark.parametrize(
    "tail", ["+uncounted line\n", "--- a/dangling.py\n", "garbage\n"]
)
def test_valid_file_cannot_hide_a_broken_tail(tail):
    text = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-before\n+after\n"
    with pytest.raises(PatchInputError):
        parse_patch(text + tail)
