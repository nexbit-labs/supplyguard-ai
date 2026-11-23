"""API key generation and hashing. The raw key never touches the database or the logs."""
import hashlib
import re
import secrets

KEY_PREFIX = "sgk_"
KEY_RANDOM_CHARS = 40
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
WELL_FORMED = re.compile(r"^sgk_[A-Za-z0-9_-]{40}$")


def generate_key() -> str:
    return KEY_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(KEY_RANDOM_CHARS))


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def key_prefix(raw: str) -> str:
    return raw[:8]


def looks_well_formed(raw: str) -> bool:
    return bool(WELL_FORMED.match(raw))
