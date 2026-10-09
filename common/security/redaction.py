"""Redaction of credentials and sensitive data (NFR-SEC-004, BR-008, Section 16.3)."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|passphrase|(^|[-_])pass($|[-_])|(^|[-_])pwd($|[-_])|secret|token|"
    r"api[-_]?key|access[-_]?key|(^|[-_])auth($|[-_])|authorization|cookie|session|credential|"
    r"private[-_]?key|card[-_]?(number|num|no)|cc[-_]?num|(^|[-_])(cvv|cvc|ssn|pin)($|[-_]))",
    re.IGNORECASE,
)

SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-access-token",
        "x-csrftoken",
        "x-project-key",
    }
)

_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    (
        re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\s*[=:]\s*[^\s&,;\"']+"),
        r"\1=" + REDACTED,
    ),
    (re.compile(r"\bwgk_[A-Za-z0-9]{8}_[A-Za-z0-9_-]{20,}"), REDACTED),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), REDACTED),
)

_CARD_CANDIDATE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2 == 1:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def _redact_card(match: re.Match[str]) -> str:
    digits = re.sub(r"[ -]", "", match.group(0))
    return "[REDACTED_CARD]" if _luhn_valid(digits) else match.group(0)


def is_sensitive_key(key: str) -> bool:
    return bool(SENSITIVE_KEY_PATTERN.search(str(key)))


def is_sensitive_header(name: str) -> bool:
    lowered = str(name).strip().lower()
    return lowered in SENSITIVE_HEADER_NAMES or is_sensitive_key(lowered)


def redact_text(value: str) -> str:
    if not value:
        return value
    for pattern, replacement in _VALUE_PATTERNS:
        value = pattern.sub(replacement, value)
    return _CARD_CANDIDATE.sub(_redact_card, value)


def redact_data(value: Any, *, depth: int = 0, max_depth: int = 8) -> Any:
    """Recursively redact sensitive keys and value patterns in JSON-like data."""
    if depth > max_depth:
        return REDACTED
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if is_sensitive_key(str(key)):
                clean[key] = REDACTED
            else:
                clean[key] = redact_data(item, depth=depth + 1, max_depth=max_depth)
        return clean
    if isinstance(value, (list, tuple)):
        return [redact_data(item, depth=depth + 1, max_depth=max_depth) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    return {k: (REDACTED if is_sensitive_header(k) else v) for k, v in (headers or {}).items()}
