from __future__ import annotations

from functools import lru_cache

from common.security.network import TargetBlockedError
from common.security.urls import UnsafeURLError, validate_target_url


class ExpectedStatusError(ValueError):
    pass


@lru_cache(maxsize=512)
def parse_expected_status(spec: str) -> tuple[tuple[int, int], ...]:
    """Parse ``"200-299,304"`` into inclusive ranges."""
    ranges: list[tuple[int, int]] = []
    for part in (spec or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            low_s, high_s = part.split("-", 1)
        else:
            low_s = high_s = part
        try:
            low, high = int(low_s), int(high_s)
        except ValueError as exc:
            raise ExpectedStatusError(f"'{part}' is not a status code or range.") from exc
        if not (100 <= low <= 599 and 100 <= high <= 599) or low > high:
            raise ExpectedStatusError(f"'{part}' is outside 100-599 or reversed.")
        ranges.append((low, high))
    if not ranges:
        raise ExpectedStatusError("At least one expected status code is required.")
    return tuple(ranges)


def status_matches(status: int, spec: str) -> bool:
    return any(low <= status <= high for low, high in parse_expected_status(spec))


def check_target(url: str) -> None:
    """Raise ``UnsafeURLError`` or ``TargetBlockedError`` when the target is not allowed."""
    validate_target_url(url, resolve=True)


__all__ = [
    "ExpectedStatusError",
    "TargetBlockedError",
    "UnsafeURLError",
    "check_target",
    "parse_expected_status",
    "status_matches",
]
