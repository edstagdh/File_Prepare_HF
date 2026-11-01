"""Password hashing (stdlib scrypt), login throttling and CSRF tokens."""

import base64
import hashlib
import hmac
import secrets
import threading
import time

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


class LoginThrottle:
    """Locks a (username, client) pair after too many failed logins."""

    def __init__(self):
        self._lock = threading.Lock()
        self._failures: dict[tuple[str, str], list[float]] = {}

    def locked_for(self, key: tuple[str, str], max_failures: int, lockout_minutes: int) -> int:
        """Seconds remaining in the lockout (0 = not locked)."""
        with self._lock:
            window = lockout_minutes * 60
            now = time.time()
            failures = [t for t in self._failures.get(key, []) if now - t < window]
            self._failures[key] = failures
            if len(failures) >= max_failures:
                return int(window - (now - failures[-max_failures]))
            return 0

    def failed(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._failures.setdefault(key, []).append(time.time())

    def succeeded(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._failures.pop(key, None)


throttle = LoginThrottle()
