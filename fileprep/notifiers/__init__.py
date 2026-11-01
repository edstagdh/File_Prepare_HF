"""Notifier registry. Add a module exposing ``async send_notification(message) -> bool`` and register it here."""

from typing import Awaitable, Callable, Optional

NotifyFn = Callable[[str], Awaitable[bool]]


def get_notifier(name: str) -> Optional[NotifyFn]:
    if name == "telegram":
        from fileprep.notifiers.telegram import send_notification
        return send_notification
    raise ValueError(f"Unknown notifier: {name}")
