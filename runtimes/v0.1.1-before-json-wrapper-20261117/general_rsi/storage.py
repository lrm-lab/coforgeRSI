from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


def digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def read_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def lock(path: Path, blocking=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise RuntimeError("Another controller already owns this goal") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".rsi",
    ".env", ".env.*", "*.pem", "*.key", "auth.json", ".DS_Store",
)


def clone_workspace(source: Path, destination: Path):
    """Fresh filesystem input per attempt. Not an OS/process/network sandbox."""
    source, destination = source.resolve(), destination.resolve()
    if destination == source or source in destination.parents:
        raise ValueError("Goal/output directories must be outside the input workspace")
    # Following external symlinks could mutate the input or read unrelated data.
    for base, directories, files in os.walk(source):
        ignored = IGNORE(base, directories + files)
        directories[:] = [x for x in directories if x not in ignored]
        for name in directories + [x for x in files if x not in ignored]:
            if (Path(base) / name).is_symlink():
                raise ValueError(f"Resolve workspace symlinks before running: {Path(base) / name}")
    shutil.copytree(source, destination, ignore=IGNORE)


class MemoryBank:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "experiences.json"

    def recall(self, question: str, limit=3):
        # Conservative lexical retrieval; applicability is still reviewed by the planner.
        words = terms(question)
        rows = read_json(self.path, [])
        ranked = []
        for item in rows:
            if item.get("status") != "validated":
                continue
            scope = terms(item.get("scope", ""))
            overlap = len(words & scope)
            if overlap:
                ranked.append((overlap, item))
        ranked.sort(key=lambda x: (-x[0], x[1]["id"]))
        return [item for _, item in ranked[:limit]]

    def deposit(self, item):
        if item.get("status") != "validated" or len(item.get("validation_refs", [])) < 2:
            raise ValueError("Only experiences with two fresh validation receipts can be deposited")
        with lock(self.root / ".memory.lock", blocking=True):
            rows = read_json(self.path, [])
            if not any(x["id"] == item["id"] for x in rows):
                rows.append(item)
                write_json(self.path, rows)

    def reject(self, item):
        with lock(self.root / ".memory.lock", blocking=True):
            path = self.root / "negative-evidence.json"
            rows = read_json(path, [])
            if not any(x["id"] == item["id"] for x in rows):
                rows.append(item)
                write_json(path, rows)

    def recall_negative(self, question, limit=3):
        words = terms(question)
        rows = read_json(self.root / "negative-evidence.json", [])
        ranked = [(len(words & terms(x.get("scope", ""))), x) for x in rows]
        ranked.sort(key=lambda x: (-x[0], x[1]["id"]))
        return [row for overlap, row in ranked if overlap][:limit]


def terms(text):
    import re
    result = set(re.findall(r"[a-z0-9_]+", text.casefold()))
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        result.update(part[i:i + 2] for i in range(len(part) - 1))
    return result


def append_feedback(goal: Path, text: str):
    import time
    with lock(goal / ".feedback.lock", blocking=True):
        rows = read_json(goal / "feedback.json", [])
        rows.append({"time": time.time(), "text": text})
        write_json(goal / "feedback.json", rows)
