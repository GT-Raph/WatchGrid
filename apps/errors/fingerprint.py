"""Deterministic error grouping (Section 16.2).

The fingerprint is versioned (``FINGERPRINT_VERSION``) because changing normalization
splits or merges existing groups; a new version must be introduced deliberately.
"""

from __future__ import annotations

import hashlib
import re

FINGERPRINT_VERSION = "v1"

_MESSAGE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<timestamp>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email>"),
    (re.compile(r"\bhttps?://\S+"), "<url>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"'[^']{1,200}'"), "'<str>'"),
    (re.compile(r'"[^"]{1,200}"'), '"<str>"'),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
)
_WHITESPACE = re.compile(r"\s+")

_PATH_SEGMENT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I), ":uuid"),
    (re.compile(r"^\d+$"), ":id"),
    (re.compile(r"^[0-9a-f]{12,}$", re.I), ":hash"),
)

_PY_FRAME = re.compile(r'File "(?P<file>[^"]+)", line \d+, in (?P<func>[\w<>.]+)')
_JS_FRAME = re.compile(r"^\s*at (?:(?P<func>[^()]+?) \()?(?P<file>[^()\s]+?):\d+(?::\d+)?\)?\s*$")
_JAVA_FRAME = re.compile(r"^\s*at (?P<func>[\w$.<>]+)\((?P<file>[^:)]+)(?::\d+)?\)\s*$")


def normalize_message(message: str) -> str:
    text = (message or "").strip()
    for pattern, replacement in _MESSAGE_RULES:
        text = pattern.sub(replacement, text)
    return _WHITESPACE.sub(" ", text)[:1000]


def normalize_endpoint(endpoint: str) -> str:
    path = (endpoint or "").split("?", 1)[0].strip()
    if not path:
        return ""
    segments = []
    for segment in path.split("/"):
        for pattern, replacement in _PATH_SEGMENT_RULES:
            if pattern.match(segment):
                segment = replacement
                break
        segments.append(segment)
    return "/".join(segments)[:500]


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def top_frame(stack_trace: str) -> str:
    """Pick a stable, line-number-free frame identifier (file + function)."""
    if not stack_trace:
        return ""
    python_frames = _PY_FRAME.findall(stack_trace)
    if python_frames:
        file, func = python_frames[-1]  # Python tracebacks list the raising frame last
        return f"{_basename(file)}:{func}"[:500]
    for line in stack_trace.splitlines():
        match = _JS_FRAME.match(line) or _JAVA_FRAME.match(line)
        if match:
            func = (match.group("func") or "<anonymous>").strip()
            return f"{_basename(match.group('file'))}:{func}"[:500]
    return ""


def compute_fingerprint(
    *,
    project_id,
    environment: str,
    error_type: str,
    normalized_message: str,
    endpoint: str,
    frame: str,
    custom: list[str] | None = None,
) -> str:
    if custom:
        material = "|".join([FINGERPRINT_VERSION, "custom", str(project_id), environment, *custom])
    else:
        material = "|".join(
            [FINGERPRINT_VERSION, str(project_id), environment, error_type, normalized_message, endpoint, frame]
        )
    return hashlib.sha256(material.encode()).hexdigest()
