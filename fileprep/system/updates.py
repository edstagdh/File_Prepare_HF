"""
Update check: compare the local VERSION with the VERSION file of the configured GitHub branch.

Channels (Settings > Application > Updates): ``stable`` follows the stable branch (master), ``dev`` the
development branch (dev), ``off`` disables the check. Nothing is downloaded or installed - the app only reports
that a newer version exists. Results are cached for a few hours.
"""

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import httpx
from loguru import logger

from fileprep import __version__

CACHE_SECONDS = 6 * 3600
MANUAL_COOLDOWN = 5 * 60  # a forced (manual) check reuses the last result when it is newer than this


@dataclass
class UpdateStatus:
    current: str
    channel: str
    branch: str = ""
    latest: Optional[str] = None
    update_available: bool = False
    ahead: bool = False  # local version is newer than the channel (e.g. a dev build on stable)
    url: str = ""  # page to read about / download the version
    error: Optional[str] = None
    checked_at: float = field(default_factory=time.time)
    fresh: bool = True  # False when a cached result was returned

    def to_dict(self) -> dict:
        data = asdict(self)
        data["retry_in"] = 0 if self.channel == "off" else max(0, int(self.checked_at + MANUAL_COOLDOWN - time.time()))
        return data


def parse_version(text: str) -> tuple[int, ...]:
    """'14.7' -> (14, 7); '14.7.1-dev' -> (14, 7, 1). Unparseable -> ()."""
    numbers = re.findall(r"\d+", text.strip().split("-")[0])
    return tuple(int(n) for n in numbers)


def compare(current: str, latest: str) -> int:
    """-1 when current < latest, 0 when equal, 1 when current > latest (missing parts count as 0)."""
    a, b = parse_version(current), parse_version(latest)
    length = max(len(a), len(b))
    a, b = a + (0,) * (length - len(a)), b + (0,) * (length - len(b))
    return (a > b) - (a < b)


_cache: Optional[UpdateStatus] = None


async def check(force: bool = False) -> UpdateStatus:
    global _cache
    from fileprep.config.store import store
    settings = store.app().updates
    if settings.channel == "off":
        return UpdateStatus(current=__version__, channel="off")
    branch = settings.stable_branch if settings.channel == "stable" else settings.dev_branch
    if _cache and _cache.channel == settings.channel and _cache.branch == branch \
            and time.time() - _cache.checked_at < (MANUAL_COOLDOWN if force else CACHE_SECONDS):
        _cache.fresh = False
        return _cache
    status = UpdateStatus(current=__version__, channel=settings.channel, branch=branch,
                          url=f"https://github.com/{settings.repository}/tree/{branch}")
    raw_url = f"https://raw.githubusercontent.com/{settings.repository}/{branch}/VERSION"
    headers = {"User-Agent": f"File_Prepare_HF/{__version__}"}
    token = store.credentials().github.token
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(raw_url, headers=headers)
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code} for {raw_url}")
        latest = response.text.strip()
        if not parse_version(latest):
            raise ValueError(f"Unexpected VERSION content: {latest[:40]!r}")
        status.latest = latest
        order = compare(__version__, latest)
        status.update_available, status.ahead = order < 0, order > 0
        if status.update_available:
            logger.warning(f"Update available: {latest} on the {settings.channel} channel ({branch}) - you have {__version__}. {status.url}")
        else:
            logger.info(f"Version check: {__version__} - latest on the {settings.channel} channel ({branch}) is {latest}")
    except (httpx.HTTPError, ValueError) as exc:
        status.error = str(exc)
        logger.warning(f"Version check failed: {exc}")
    _cache = status
    return status
