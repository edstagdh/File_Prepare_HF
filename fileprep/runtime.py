"""
Per-job runtime context.

Processing code deep inside the pipeline (TPDB matching, cover handling, previews...) needs a few
job-scoped services: the job's settings snapshot, a way to ask the user a question, progress reporting
and cooperative stop checks. Passing them through every call would touch hundreds of signatures, so they
live in a :class:`contextvars.ContextVar` that the job runner sets once; asyncio tasks inherit it.

Outside a job (CLI / tests) a console runtime backed by the saved settings is used.
"""

import asyncio
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Protocol

from loguru import logger

from fileprep.config.models import JobConfig


@dataclass
class Question:
    """A question for the user. ``choices`` are quick answers; free text is always accepted."""
    text: str
    title: str = "Input required"
    choices: list[str] = field(default_factory=list)
    multiline: bool = False
    secret: bool = False
    kind: str = "text"  # "text" or "list" (several entries answered at once, as a JSON array)
    item_label: str = ""  # list questions: label of one entry, e.g. "Performer"


class Prompter(Protocol):
    async def ask(self, question: Question) -> str: ...


class ConsolePrompter:
    """Terminal prompter. Writes the prompt to stderr (loguru's stream) so it stays ordered with logs."""

    async def ask(self, question: Question) -> str:
        await logger.complete()
        hint = f" [{'/'.join(question.choices)}]" if question.choices and "/" not in question.text else ""
        sys.stderr.write(f"{question.text}{hint}\n> " if not question.text.endswith((": ", "\n")) else question.text)
        sys.stderr.flush()
        return await asyncio.to_thread(input)


NotifyFn = Optional[Callable[[str], Awaitable[bool]]]


@dataclass
class Runtime:
    config: JobConfig
    prompter: Prompter
    job_id: Optional[str] = None
    notify: NotifyFn = None
    progress_callback: Optional[Callable[[str, Optional[float], str], None]] = None
    stop_requested: Callable[[], bool] = lambda: False


_current: ContextVar[Optional[Runtime]] = ContextVar("fileprep_runtime", default=None)
_console_runtime: Optional[Runtime] = None


def current() -> Runtime:
    rt = _current.get()
    if rt is not None:
        return rt
    global _console_runtime
    if _console_runtime is None:
        from fileprep.config.store import store
        _console_runtime = Runtime(config=store.job_config(), prompter=ConsolePrompter())
    return _console_runtime


@contextmanager
def use_runtime(runtime: Runtime):
    token = _current.set(runtime)
    try:
        yield runtime
    finally:
        _current.reset(token)


def config() -> JobConfig:
    return current().config


_CHOICE_PATTERNS = [
    (re.compile(r"\(yes\[y\]/no\[n\]/cancel\[c\]\)", re.I), ["yes", "no", "cancel"]),
    (re.compile(r"\(\[?y\]?es/\[?n\]?o\)|\(yes/no\)|\(y/n\)", re.I), ["yes", "no"]),
    (re.compile(r"\[K/R\]", re.I), ["k", "r"]),
    (re.compile(r"\(O\)riginal.*\(I\)nput", re.I), ["o", "i"]),
]


def suggest_choices(text: str) -> list[str]:
    for pattern, choices in _CHOICE_PATTERNS:
        if pattern.search(text):
            return choices
    return []


async def prompt(text: str, *, title: str = "Input required", choices: list[str] | None = None,
                 multiline: bool = False) -> str:
    """Ask the user a question through the active runtime (web UI inside a job, terminal otherwise).

    Returns the raw answer string, exactly like ``input()`` (not stripped / lower-cased)."""
    question = Question(text=text, title=title, choices=choices if choices is not None else suggest_choices(text),
                        multiline=multiline)
    return await current().prompter.ask(question)


async def prompt_list(text: str, *, title: str = "Input required", item_label: str = "Entry") -> list[str]:
    """Ask for several values at once. In the web UI this is one form with a field per entry ("+ Add another");
    on a terminal the entries are typed one per line, finished with an empty line. Returns the non-empty entries."""
    import json
    prompter = current().prompter
    if getattr(prompter, "supports_list", False):
        raw = await prompter.ask(Question(text=text, title=title, kind="list", item_label=item_label))
        try:
            values = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError:
            values = raw.splitlines()
        return [str(v).strip() for v in values if str(v).strip()]
    logger.info(f"{text} (one per line, empty line to finish)")
    values = []
    while True:
        entry = (await prompter.ask(Question(text=f"{item_label} {len(values) + 1}: ", title=title))).strip()
        if not entry:
            return values
        values.append(entry)


def report_progress(stage: str, percent: Optional[float] = None, detail: str = "") -> None:
    callback = current().progress_callback
    if callback:
        try:
            callback(stage, percent, detail)
        except Exception:  # progress must never break processing
            logger.opt(exception=True).debug("progress callback failed")


def stop_requested() -> bool:
    return current().stop_requested()
