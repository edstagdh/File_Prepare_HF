"""
Shared exception types and the rule for how failures are logged.

Expected failures - the user cancelled, a network / host / browser / file problem, a known application error -
are logged as one line ("<what failed>: <reason>") without a traceback, everywhere. Only unexpected exceptions
(programming errors) keep their traceback, and only in the application log file in Logs/; the console and the
job log always show one line.
"""

import asyncio
import concurrent.futures


class UserCancelled(Exception):
    """The user answered 'cancel' to a question (tracker confirmation, 2FA, ...)."""


class ExpectedFailure(Exception):
    """A failure with a clear message that needs no traceback."""


def _expected_types() -> tuple[type, ...]:
    types: list[type] = [UserCancelled, ExpectedFailure, asyncio.CancelledError, concurrent.futures.CancelledError,
                         TimeoutError, ConnectionError, OSError]
    try:
        import httpx
        types.append(httpx.HTTPError)
    except ImportError:
        pass
    try:
        from selenium.common.exceptions import WebDriverException
        types.append(WebDriverException)
    except ImportError:
        pass
    from fileprep.config.store import ConfigError
    from fileprep.tpdb.api_client import TpdbConnectionError
    types += [ConfigError, TpdbConnectionError]
    return tuple(types)


_EXPECTED: tuple[type, ...] | None = None
_OWN_ERRORS = {"JobError", "PlanOutdated", "SyncError"}


def is_expected(exc: BaseException) -> bool:
    global _EXPECTED
    if _EXPECTED is None:
        try:
            _EXPECTED = _expected_types()
        except Exception:  # during early start-up imports may not be ready yet
            return isinstance(exc, (UserCancelled, ExpectedFailure, asyncio.CancelledError, OSError)) or type(exc) is RuntimeError
    # plain RuntimeError is what this code raises for known failures ("Step failed: cover"); its subclasses
    # (RecursionError, NotImplementedError) are bugs
    return isinstance(exc, _EXPECTED) or type(exc) is RuntimeError or type(exc).__name__ in _OWN_ERRORS


def describe(exc: BaseException) -> str:
    """Short one-line description: the message for our own errors, 'Type: message' otherwise."""
    text = " ".join(str(exc).split())
    if isinstance(exc, (UserCancelled, ExpectedFailure)) or type(exc).__name__ in ("RuntimeError", "JobError"):
        return text or type(exc).__name__
    if hasattr(exc, "msg") and type(exc).__module__.startswith("selenium"):  # selenium: drop the stacktrace part
        text = " ".join(str(exc.msg or "").split())
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
