"""Read Git or unified patches and expose added lines in post-change coordinates."""

import re
from codecs import escape_decode
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha1, sha256
from io import StringIO
from pathlib import Path
from typing import Literal

from unidiff import Hunk, PatchedFile, PatchSet, UnidiffParseError
from unidiff.patch import Line


class PatchInputError(ValueError):
    """The patch could not be read as a complete diff."""


@dataclass
class FileChange:
    old_path: str | None
    new_path: str | None
    status: Literal["added", "deleted", "modified", "renamed", "copied"]
    is_binary: bool
    hunks: tuple[Hunk, ...]
    new_blob: str | None = None

    @property
    def added_lines(self) -> Iterator[Line]:
        """Yield additions in patch order. target_line_no is the 1-based new-file line."""
        return (line for hunk in self.hunks for line in hunk if line.is_added)


def _path(raw: str) -> str | None:
    if raw == "/dev/null":
        return None
    if raw.startswith('"') and raw.endswith('"'):
        # Git C-quotes special characters and can write UTF-8 bytes as octal escapes.
        try:
            raw = escape_decode(raw[1:-1].encode())[0].decode(
                "utf-8", "surrogateescape"
            )
        except ValueError as exc:
            raise PatchInputError("Invalid quoted path in patch") from exc
    if not raw:
        raise PatchInputError("Empty file path in patch")
    return raw


def _file_change(file: PatchedFile) -> FileChange:
    old, new = _path(file.source_file), _path(file.target_file)
    # Strip the conventional pair together so plain paths like a/module.py survive.
    if (old is None or old.startswith("a/")) and (new is None or new.startswith("b/")):
        old = old[2:] if old is not None else None
        new = new[2:] if new is not None else None
    if old == "" or new == "" or (old is None and new is None):
        raise PatchInputError("Missing file path in patch")
    if old is None:
        status = "added"
    elif new is None:
        status = "deleted"
    elif any(line.startswith("copy from ") for line in file.patch_info or ()):
        status = "copied"
    elif old != new:
        status = "renamed"
    else:
        status = "modified"
    new_blob = None
    for header in file.patch_info or ():
        if match := re.match(r"index [0-9a-f]+\.\.([0-9a-f]+)\b", header):
            new_blob = match[1]
    return FileChange(old, new, status, file.is_binary_file, tuple(file), new_blob)


_GIT_METADATA = re.compile(
    r"(index) [0-9a-f]+\.\.[0-9a-f]+(?: [0-7]{6})?"
    r"|(old mode|new mode|new file mode|deleted file mode) [0-7]{6}"
    r"|((?:dis)?similarity index) \d+%|((?:rename|copy) (?:from|to)) .+"
)


def _metadata_only_change(metadata: dict[str, str]) -> bool:
    if "index" in metadata:
        old, new = metadata["index"].split()[0].split("..")
    if "new file mode" in metadata or "deleted file mode" in metadata:
        # A hunkless creation/deletion can only describe an empty blob.
        if "index" not in metadata:
            return True
        blob = new if "new file mode" in metadata else old
        return any(
            digest(b"blob 0\0").hexdigest().startswith(blob)
            for digest in (sha1, sha256)
        )
    if "index" in metadata and old != new:
        return False
    if any({f"{op} from", f"{op} to"} <= metadata.keys() for op in ("copy", "rename")):
        return metadata.get("similarity index", "100%") == "100%"
    if {"old mode", "new mode"} <= metadata.keys():
        return metadata["old mode"] != metadata["new mode"]
    return False


def _validate_file(file: PatchedFile, lines: list[str], start: int, end: int) -> None:
    """Check the input around the hunks that unidiff otherwise treats as free text."""
    i = start
    metadata = {}
    if lines[i].startswith("diff --git "):
        i += 1
        while i < end and (match := _GIT_METADATA.fullmatch(lines[i].rstrip("\n"))):
            key = match[match.lastindex]
            metadata[key] = match[0][len(key) + 1 :]
            i += 1

    if file.is_binary_file:
        if i < end and lines[i].startswith("GIT binary patch"):
            # The payload is opaque to a line reviewer, but must contain a Git block.
            blocks = "".join(lines[i + 1 : end]).strip("\n").split("\n\n")
            if all(
                re.fullmatch(r"(?:literal|delta) \d+(?:\n[A-Za-z][!-~]+)+", b)
                for b in blocks
            ):
                return
        elif i + 1 == end and re.fullmatch(
            r"Binary files? .+ (?:differ|has changed)\n?", lines[i]
        ):
            return
        raise PatchInputError(f"Incomplete binary patch at line {i + 1}")

    if not file:
        if i == end and _metadata_only_change(metadata):
            return
        raise PatchInputError(f"Incomplete file change at line {start + 1}")

    for marker, path in (("--- ", file.source_file), ("+++ ", file.target_file)):
        if i >= end or not lines[i].startswith(marker):
            raise PatchInputError(
                f"Expected {marker.strip()} file header at line {i + 1}"
            )
        if lines[i][4:].rstrip("\n").split("\t", 1)[0] != path:
            raise PatchInputError(f"Inconsistent file path at line {i + 1}")
        i += 1

    for hunk in file:
        if not hunk or not lines[i].startswith("@@ ") or hunk[0].diff_line_no != i + 2:
            raise PatchInputError(f"Expected hunk at line {i + 1}")
        if (
            (
                hunk.source_length
                and (hunk.source_start == 0 or file.source_file == "/dev/null")
            )
            or (
                hunk.target_length
                and (hunk.target_start == 0 or file.target_file == "/dev/null")
            )
            or not (hunk.added or hunk.removed)
        ):
            raise PatchInputError(f"Invalid hunk range or empty change at line {i + 1}")
        i += 1
        for line in hunk:
            if (
                line.line_type == "\\"
                and lines[i].rstrip("\n") != r"\ No newline at end of file"
            ):
                raise PatchInputError(f"Invalid newline marker at line {i + 1}")
            i += 1
    if i != end:
        raise PatchInputError(f"Unexpected patch content at line {i + 1}")


def parse_patch(text: str) -> tuple[FileChange, ...]:
    """Parse raw unified diff text. An empty diff contains zero file changes."""
    lines = StringIO(text.replace("\r\n", "\n")).readlines()
    if not lines:
        return ()
    try:
        files = PatchSet(lines)
    except UnidiffParseError as exc:
        # unidiff appends the rejected raw patch line to some diagnostics.
        reason = str(exc).partition(":")[0]
        raise PatchInputError(f"Malformed patch: {reason}") from None
    except ValueError:
        raise PatchInputError("Malformed patch: invalid hunk data") from None
    # For a plain unified diff, unidiff creates the file on the +++ header.
    starts = []
    for file in files:
        start = file.diff_line_no - 1
        if lines[start].startswith("+++ "):
            start -= 1
        starts.append(start)
    if not starts or starts[0] != 0:
        raise PatchInputError("Expected a file patch at line 1")
    changes = []
    for file, start, end in zip(files, starts, [*starts[1:], len(lines)], strict=True):
        _validate_file(file, lines, start, end)
        changes.append(_file_change(file))
    return tuple(changes)


def read_patch(path: Path) -> tuple[FileChange, ...]:
    """Read a UTF-8 patch from disk and identify the file in input diagnostics."""
    try:
        return parse_patch(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, PatchInputError) as exc:
        raise PatchInputError(f"{path}: {exc}") from exc
