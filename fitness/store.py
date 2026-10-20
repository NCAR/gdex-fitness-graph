"""File-backed repository for the fitness-graph document.

All reads that feed a write, the revision check, validation, mutation and the
atomic replacement happen under one exclusive file lock (POSIX ``flock``).
Operational files (lock, backups, staged imports) live in ``.fitness-local/``
next to the data file and are git-ignored.
"""
import copy
import fcntl
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import schema

STAGED_RE = re.compile(r"^[0-9a-f]{64}$")


class StorageError(Exception):
    """The storage file is unreadable or invalid. It is never overwritten."""

    def __init__(self, message, details=()):
        self.details = list(details)
        super().__init__(message)


class ConflictError(Exception):
    """The caller's revision is stale."""

    def __init__(self, expected, current):
        self.expected = expected
        self.current = current
        super().__init__(f"Expected revision {expected}, current is {current}.")


@dataclass(frozen=True)
class Snapshot:
    doc: dict
    revision: str


class GraphStore:
    def __init__(self, path):
        self.path = Path(path)
        self.state_dir = self.path.parent / ".fitness-local"

    # -- reading ----------------------------------------------------------

    def _read_unlocked(self):
        if not self.path.exists():
            doc = schema.empty_document()
            return Snapshot(doc, schema.revision_of(doc))
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageError(
                f"{self.path} could not be read as JSON ({exc}). "
                "It has not been modified; fix or restore the file (e.g. with git) and reload."
            ) from exc
        try:
            doc = schema.load_document(raw)
        except schema.SchemaError as exc:
            raise StorageError(
                f"{self.path} is not a valid fitness-graph document. "
                "It has not been modified; fix or restore the file and reload.",
                exc.errors,
            ) from exc
        return Snapshot(doc, schema.revision_of(doc))

    def read(self):
        """Return the current document and its revision (no lock needed: writes are atomic)."""
        return self._read_unlocked()

    # -- writing ----------------------------------------------------------

    @contextmanager
    def _lock(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_dir / f"{self.path.name}.lock", "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def update(self, expected_revision, mutate, *, backup=False, replace=False):
        """Apply ``mutate(doc)`` if ``expected_revision`` is still current.

        ``mutate`` receives a private copy and modifies it in place; its return value
        is ignored unless ``replace=True``, in which case it must return the complete
        replacement document. Errors it raises propagate and nothing is written.
        With ``backup=True`` the existing file is first copied to the backups dir.
        """
        with self._lock():
            current = self._read_unlocked()
            if expected_revision != current.revision:
                raise ConflictError(expected_revision, current.revision)
            candidate = copy.deepcopy(current.doc)
            result = mutate(candidate)
            new_doc = schema.load_document(result if replace else candidate)
            if backup and self.path.exists():
                self._backup(current.revision)
            self._write_atomic(new_doc)
            return Snapshot(new_doc, schema.revision_of(new_doc))

    def _backup(self, revision):
        backups = self.state_dir / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = backups / f"{self.path.stem}-{stamp}-{revision}.json"
        shutil.copy2(self.path, target)
        return target

    def _write_atomic(self, doc):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(schema.dumps(doc))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def backups(self):
        folder = self.state_dir / "backups"
        return sorted(folder.glob("*.json")) if folder.exists() else []

    # -- staged imports ---------------------------------------------------
    # An uploaded file is validated, normalized, and stored under the SHA-256 of
    # its canonical text. Confirming an import names only that hash, so the
    # document applied is byte-for-byte the one that was validated, and the
    # browser never supplies a filesystem path.

    def stage_import(self, doc):
        text = schema.dumps(doc)
        digest = schema.content_hash(doc)
        folder = self.state_dir / "imports"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{digest}.json").write_text(text, encoding="utf-8")
        return digest

    def load_staged(self, digest):
        if not STAGED_RE.match(digest or ""):
            raise StorageError("Invalid import token.")
        target = self.state_dir / "imports" / f"{digest}.json"
        try:
            text = target.read_text(encoding="utf-8")
            doc = schema.load_document(json.loads(text))
        except (OSError, ValueError, schema.SchemaError) as exc:
            raise StorageError(
                "The validated import is no longer available; upload the file again."
            ) from exc
        if schema.content_hash(doc) != digest:
            raise StorageError("The staged import does not match its token; upload the file again.")
        return doc

    def discard_staged(self, digest):
        if STAGED_RE.match(digest or ""):
            (self.state_dir / "imports" / f"{digest}.json").unlink(missing_ok=True)

    def apply_import(self, expected_revision, digest):
        new_doc = self.load_staged(digest)
        snapshot = self.update(expected_revision, lambda _doc: new_doc, backup=True, replace=True)
        self.discard_staged(digest)
        return snapshot
