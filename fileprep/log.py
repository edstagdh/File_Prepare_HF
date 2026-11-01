"""Logging setup (console + rotating application log in Logs/). Job logs are added by the job manager."""

import sys

from loguru import logger

from fileprep import paths


def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(sys.stderr, level=level,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {extra[job_tag]}<level>{message}</level>",
               filter=_tag_job)
    paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(paths.LOGS_DIR / "App_Log_{time:YYYY.MMMM.DD}.log", rotation="5 days", backtrace=True, enqueue=False, catch=True,
               level="DEBUG", filter=_tag_job,
               format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[job_tag]}{name}:{function}:{line} - {message}")


def _tag_job(record) -> bool:
    job_id = record["extra"].get("job_id")
    record["extra"]["job_tag"] = f"[{job_id}] " if job_id else ""
    return True
