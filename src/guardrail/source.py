"""Resolve a review's patch and the complete Python sources available to checks."""

import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property
from hashlib import sha1, sha256
from io import BytesIO
from pathlib import Path

from unidiff import Hunk

from guardrail.patch import FileChange, parse_patch, read_patch
from guardrail.secrets import SecretRedactor


class SourceError(ValueError):
    """A review input could not be resolved."""


@dataclass
class ReviewSource:
    changes: tuple[FileChange, ...]
    python_files: dict[str, bytes]
    skipped_reason: str | None = None

    @cached_property
    def redactor(self) -> SecretRedactor:
        return SecretRedactor(self.changes)

    def prepare_model_hunk(self, path: str, hunk: Hunk) -> str:
        """Prepare a located, redacted excerpt for a model without sending it."""
        return self.redactor.redact(
            f"{path}\n{hunk}", bare_assignments=not path.endswith(".py")
        )

    def changed_python_lines(self) -> Iterator[tuple[str, set[int]]]:
        """Yield source-backed Python files with nonblank, noncomment additions."""
        for change in self.changes:
            path = change.new_path
            if path not in self.python_files or change.is_binary:
                continue
            added = {
                line.target_line_no
                for line in change.added_lines
                if line.value.strip() and not line.value.lstrip().startswith("#")
            }
            if added:
                yield path, added


def _git(directory: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", directory, *args],
            input=stdin,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise SourceError(f"Could not run Git: {exc}") from exc
    if result.returncode:
        raise SourceError(
            result.stderr.decode(errors="replace").strip()
            or f"git {args[0]} failed (exit {result.returncode})"
        )
    return result.stdout


def _commit(directory: Path, ref: str) -> str:
    return (
        _git(directory, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
        .decode("ascii")
        .strip()
    )


def revision_source(directory: Path, base: str, head: str) -> ReviewSource:
    base_id, head_id = _commit(directory, base), _commit(directory, head)
    ancestor = _git(directory, "merge-base", base_id, head_id).decode("ascii").strip()
    patch = _git(
        directory,
        "-c",
        "core.quotePath=false",
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--no-relative",
        "--full-index",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--find-renames",
        "--submodule=short",
        ancestor,
        head_id,
        "--",
    )
    changes = parse_patch(patch.decode("utf-8", "surrogateescape"))

    tree = _git(directory, "ls-tree", "-rz", "--full-tree", head_id)
    blobs = {}
    for entry in tree.split(b"\0"):
        if entry:
            header, path = entry.split(b"\t", 1)
            mode, _, oid = header.split()
            if mode in (b"100644", b"100755") and path.endswith(b".py"):
                blobs[os.fsdecode(path)] = oid

    files = {}
    if blobs:
        batch = BytesIO(
            _git(
                directory,
                "cat-file",
                "--batch",
                stdin=b"\n".join(blobs.values()) + b"\n",
            )
        )
        for path, oid in blobs.items():
            header = batch.readline().split()
            if len(header) != 3 or header[:2] != [oid, b"blob"]:
                raise SourceError(f"Could not read head blob for {path}")
            files[path] = batch.read(int(header[2]))
            if batch.read(1) != b"\n":
                raise SourceError(f"Incomplete head blob for {path}")
    return ReviewSource(changes, files)


def _matches(change: FileChange, data: bytes) -> bool:
    if change.new_blob:
        # Git often stores LF blobs in repositories with CRLF working trees.
        candidates = [data]
        if b"\r\n" in data and not change.is_binary:
            candidates.append(data.replace(b"\r\n", b"\n"))
        if not any(
            digest(b"blob %d\0" % len(raw) + raw)
            .hexdigest()
            .startswith(change.new_blob)
            for raw in candidates
            for digest in (sha1, sha256)
        ):
            return False

    if not change.hunks:
        return bool(change.new_blob) or (change.status == "added" and data == b"")

    lines = BytesIO(data.replace(b"\r\n", b"\n")).readlines()
    evidence = bool(change.new_blob)
    for hunk in change.hunks:
        expected = []
        previous_target = False
        for line in hunk:
            if line.line_type == "\\":
                if previous_target:
                    expected[-1] = expected[-1].removesuffix(b"\n")
            else:
                previous_target = line.is_added or line.is_context
                if previous_target:
                    expected.append(line.value.encode("utf-8", "surrogateescape"))
        start = hunk.target_start - 1 if expected else hunk.target_start
        if start > len(lines) or lines[start : start + len(expected)] != expected:
            return False
        evidence |= bool(expected)
    if change.status == "added":
        return sum(h.target_length for h in change.hunks) == len(lines)
    return evidence


def _checkout_files(
    directory: Path, changes: tuple[FileChange, ...]
) -> dict[str, bytes]:
    root = Path(
        os.fsdecode(_git(directory, "rev-parse", "--show-toplevel").rstrip(b"\r\n"))
    )
    listing = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    paths = {os.fsdecode(p) for p in listing.split(b"\0") if p}

    # Only open names supplied by Git. Symlinks and junctions are not source files.
    def regular(name: str) -> bool:
        if name not in paths:
            return False
        path = root / name
        return path.resolve() == path and path.is_file()

    files = {}
    targets = {c.new_path for c in changes if c.new_path is not None}
    for change in changes:
        if (
            change.status in ("deleted", "renamed")
            and change.old_path not in targets
            and change.old_path in paths
            and os.path.lexists(root / change.old_path)
        ):
            raise SourceError(f"{change.old_path} still exists in the checkout")
        name = change.new_path
        if name is None:
            continue
        if not regular(name):
            raise SourceError(f"missing regular post-change file {name}")
        data = (root / name).read_bytes()
        if not _matches(change, data):
            raise SourceError(f"cannot match {name} to the patch post-image")
        if name.endswith(".py"):
            files[name] = data

    if changes and not targets:
        raise SourceError("deletions alone do not identify the post-change checkout")
    for name in sorted(paths):
        if name.endswith(".py") and name not in files and regular(name):
            files[name] = (root / name).read_bytes()
    return files


def patch_source(patch: Path, checkout: Path | None) -> ReviewSource:
    changes = read_patch(patch)
    if checkout is None:
        return ReviewSource(changes, {}, "patch-only review requested")
    try:
        files = _checkout_files(checkout, changes)
    except (SourceError, OSError, RuntimeError) as exc:
        return ReviewSource(changes, {}, str(exc))
    return ReviewSource(changes, files)
