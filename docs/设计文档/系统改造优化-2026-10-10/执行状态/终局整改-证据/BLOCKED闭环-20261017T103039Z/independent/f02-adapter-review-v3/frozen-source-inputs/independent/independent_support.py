"""Artifact-only helpers. No application imports, DB clients or network calls."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path


class MissingEvidence(Exception):
    """Missing/unsupported evidence is BLOCKED, never an implicit green."""


class InvalidEvidence(Exception):
    """Contradictory evidence is REWORK."""


def need(value, message):
    if value is None:
        raise MissingEvidence(message)
    return value


def finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def instant(value, naive_timezone="UTC"):
    if not isinstance(value, str):
        raise MissingEvidence(f"timestamp is absent/not a string: {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidEvidence(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        if naive_timezone != "UTC":
            raise MissingEvidence(
                "naive timestamps require an explicitly frozen UTC convention"
            )
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_digest(value):
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class ReadSet:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.files = []

    def path(self, ref):
        if not isinstance(ref, dict) or not ref.get("path") or not ref.get("sha256"):
            raise MissingEvidence("every artifact reference needs path and SHA-256")
        path = (self.root / ref["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise InvalidEvidence(
                f"artifact escapes the explicitly supplied evidence root: {path}"
            )
        if not path.is_file():
            raise MissingEvidence(f"artifact is absent: {path}")
        actual = digest(path)
        if actual != ref["sha256"]:
            raise InvalidEvidence(
                f"SHA-256 mismatch: {path}: expected {ref['sha256']}, got {actual}"
            )
        if "size" in ref and path.stat().st_size != ref["size"]:
            raise InvalidEvidence(f"size mismatch: {path}")
        item = {"path": str(path), "sha256": actual, "size": path.stat().st_size}
        if item not in self.files:
            self.files.append(item)
        return path

    def json(self, ref):
        path = self.path(ref)
        try:
            return json.loads(path.read_text())
        except (UnicodeError, ValueError) as exc:
            raise InvalidEvidence(f"invalid JSON: {path}: {exc}") from exc

    def verify_unchanged(self):
        for item in self.files:
            path = Path(item["path"])
            if (
                not path.is_file()
                or path.stat().st_size != item["size"]
                or digest(path) != item["sha256"]
            ):
                raise InvalidEvidence(
                    f"artifact changed during independent read: {path}"
                )


def write_new(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Do not overwrite a historical expectation, receipt or evidence file.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def verdict(issues):
    if any(item["level"] == "REWORK" for item in issues):
        return "REWORK"
    if issues:
        return "BLOCKED"
    return "PASS"


def result_rc(value):
    return {"PASS": 0, "REWORK": 1, "BLOCKED": 2}[value]
