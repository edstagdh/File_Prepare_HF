"""
Shared helpers for the "maybe send a push notification, then block on
user input" pattern that repeats throughout the scene-matching flow.
"""

import asyncio
import sys
from typing import Awaitable, Callable, Optional

from loguru import logger

# An optional async callback that pushes a notification and reports success.
NotifyFn = Optional[Callable[[str], Awaitable[bool]]]


async def notify_and_pause(send_notification: NotifyFn, message: str) -> None:
    """
    Fire the optional push notification and give the UI a brief moment
    before a blocking ``prompt()`` call.

    Args:
        send_notification: Optional async callback that sends a push
            notification when user input is required; skipped entirely if
            falsy.
        message: Notification text describing what input is required.
    """
    if send_notification:
        result = await send_notification(message)
        if not result:
            logger.warning("Notifier failed to send user input request.")
    await asyncio.sleep(0.5)


async def prompt(text: str) -> str:
    """
    Drop-in replacement for the builtin ``input()`` that keeps prompt text
    in the same order as surrounding log lines on screen.

    ``input(text)`` writes its prompt to stdout. loguru's default sink
    writes to stderr. Those are two independent streams - most terminals
    and IDE consoles read and display stdout/stderr as separate,
    asynchronously-polled channels, so even if this process writes to them
    in perfect order (and even flushes both immediately), the thing
    rendering the console has no obligation to display them in that same
    order. That mismatch is what causes a prompt to visually appear before
    or after a log line that was actually written first/second in the
    code. There's no fix for this that stays within one stream but writes
    to two - the two writes have to become one.

    So instead of passing ``text`` to ``input()`` (-> stdout), this writes
    ``text`` directly to stderr (matching loguru's default sink) and then
    calls bare ``input()`` to just block on the response. Writing to the
    exact same stream loguru uses means the OS serializes the two writes
    in true program order, since there's no longer a second stream for a
    console to interleave against.

    ``await logger.complete()`` still runs first, for the separate reason
    that a logger configured with ``enqueue=True`` returns from
    ``logger.info(...)`` as soon as the record is queued, not once it's
    written - this drains that queue so a message logged just before this
    call is guaranteed to already be in the stream before the prompt text
    is appended to it.

    Note: this assumes stderr is where your logger actually writes,
    which is loguru's default. If your setup calls
    ``logger.add(sys.stdout, ...)`` (or a file, or another sink) instead,
    this won't line up - swap the ``sys.stderr`` below for wherever your
    sink actually points.

    Args:
        text: The prompt text, written to stderr in place of being passed
            to ``input()``.

    Returns:
        The raw string the user typed (not stripped or lowercased - same
        as calling ``input()`` directly).
    """
    await logger.complete()
    sys.stderr.write(text)
    sys.stderr.flush()
    return input()


def parse_keep_selection(raw: str, count: int) -> Optional[set]:
    """
    Parse a comma-separated "keep these numbers" response into a set of
    1-based indices.

    Args:
        raw: Raw user input, e.g. "1,3" or "all".
        count: Number of items available, used only for documentation of
            the expected index range (not validated here).

    Returns:
        A set of ints if ``raw`` parses as digits, or None if ``raw`` is
        "all"/empty (meaning "keep everything") or unparsable.
    """
    if raw in ("all", ""):
        return None
    try:
        return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}
    except ValueError:
        logger.warning("Invalid input for selection, keeping all items.")
        return None
