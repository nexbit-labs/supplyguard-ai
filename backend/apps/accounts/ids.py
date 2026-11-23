"""The one place that formats and parses the API user id (`USR-<n>`, docs/CONTRACTS.md s10)."""
import re

USER_ID_PREFIX = "USR-"
_USER_ID_RE = re.compile(r"^USR-(\d+)$")


def format_user_id(pk: int) -> str:
    return f"{USER_ID_PREFIX}{int(pk)}"


def parse_user_id(value: str) -> int:
    """Return the integer pk for `USR-<n>`; raise ValueError for anything else."""
    match = _USER_ID_RE.match(value or "")
    if not match:
        raise ValueError(f"not a user id: {value!r}")
    return int(match.group(1))
