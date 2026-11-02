"""Logging setup (console + rotating application log in Logs/). Job logs are added by the job manager."""

import sys
import threading

from loguru import logger

from fileprep import paths
from fileprep.errors import describe, is_expected


def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.configure(patcher=_one_line_failures)
    logger.add(sys.stderr, level=level, filter=_tag_job, format=_console_format)
    paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(paths.LOGS_DIR / "App_Log_{time:YYYY.MMMM.DD}.log", rotation="5 days", backtrace=False, enqueue=False, catch=True,
               level="DEBUG", filter=_tag_job, format=_file_format)
    threading.excepthook = _thread_exception


def _thread_exception(args) -> None:
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread else "thread"
    logger.opt(exception=(args.exc_type, args.exc_value, args.exc_traceback)).error(f"{name} stopped")


def asyncio_exception_handler(loop, context) -> None:
    """Event loop handler: background task errors ("Task exception was never retrieved") as one log line."""
    exc = context.get("exception")
    message = context.get("message", "Background task failed")
    if exc is not None:
        logger.opt(exception=exc).error(message)
    else:
        logger.warning(message)


def _one_line_failures(record) -> None:
    """Add the exception's short description to the message; drop the traceback of expected failures
    (cancellations, network / host / browser / file errors). See fileprep/errors.py."""
    exc = record["exception"]
    if not exc or exc.value is None:
        return
    text = describe(exc.value)
    raw = str(exc.value).strip()
    if text and text not in record["message"] and not (raw and raw in record["message"]):
        record["message"] = f"{record['message']}: {text}"
    if is_expected(exc.value):
        record["exception"] = None


def _console_format(record) -> str:
    # never a traceback on the console; unexpected errors point to the log file
    hint = " (details in the log file in Logs/)" if record["exception"] else ""
    return ("<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {extra[job_tag]}<level>{message}</level>"
            + hint + "\n")


def _file_format(record) -> str:
    base = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[job_tag]}{name}:{function}:{line} - {message}\n"
    return base + "{exception}" if record["exception"] else base


def job_log_format(record) -> str:
    """Format of the per-job log (job page and data/jobs/<id>.log): one line, never a traceback."""
    hint = " (details in the log file in Logs/)" if record["exception"] else ""
    return "{time:HH:mm:ss} | {level: <8} | {message}" + hint + "\n"


def _tag_job(record) -> bool:
    job_id = record["extra"].get("job_id")
    record["extra"]["job_tag"] = f"[{job_id}] " if job_id else ""
    return True
