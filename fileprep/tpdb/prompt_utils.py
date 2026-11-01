"""
Shared helpers for the "maybe send a push notification, then block on
user input" pattern that repeats throughout the scene-matching flow.
"""

import asyncio
from typing import Awaitable, Callable, Optional

from loguru import logger

from fileprep.runtime import prompt as runtime_prompt

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
    Drop-in replacement for the builtin ``input()``.

    Routed through :func:`fileprep.runtime.prompt`, so inside a job the question is shown in the web UI
    (together with the latest log lines, which carry the candidate lists printed before each prompt) and
    on a terminal it is written to stderr - loguru's stream - so it stays in order with the log output.

    Returns:
        The raw string the user typed (not stripped or lowercased - same as ``input()``).
    """
    return await runtime_prompt(text, title="TPDB matching")


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
