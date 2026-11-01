import asyncio

import httpx

from fileprep import __version__
from fileprep.config.models import AppConfig, Credentials
from fileprep.system import updates

REAL_CLIENT = httpx.AsyncClient


def test_compare_versions():
    assert updates.compare("14.7", "14.8") == -1
    assert updates.compare("14.8", "14.8.0") == 0
    assert updates.compare("14.10", "14.9") == 1
    assert updates.parse_version("14.7.1-dev") == (14, 7, 1)


def _check(monkeypatch, channel, remote_version, status=200):
    app = AppConfig()
    app.updates.channel = channel
    monkeypatch.setattr(updates, "_cache", None)
    from fileprep.config import store as store_module
    monkeypatch.setattr(store_module.store, "app", lambda: app)
    monkeypatch.setattr(store_module.store, "credentials", lambda: Credentials())
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(status, text=remote_version)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    return asyncio.run(updates.check(force=True)), seen


def test_update_available_on_stable_channel(monkeypatch):
    result, seen = _check(monkeypatch, "stable", "999.0\n")
    assert result.update_available and result.latest == "999.0" and result.branch == "master"
    assert seen == ["https://raw.githubusercontent.com/edstagdh/File_Prepare_HF/master/VERSION"]


def test_dev_channel_up_to_date_and_ahead(monkeypatch):
    result, seen = _check(monkeypatch, "dev", __version__)
    assert not result.update_available and not result.ahead and seen[0].endswith("/dev/VERSION")
    result, _ = _check(monkeypatch, "stable", "1.0")
    assert result.ahead and not result.update_available


def test_failures_and_off(monkeypatch):
    result, _ = _check(monkeypatch, "stable", "Not Found", status=404)
    assert result.error and not result.update_available
    result, seen = _check(monkeypatch, "off", "999.0")
    assert result.channel == "off" and not seen


def test_manual_check_cooldown(monkeypatch):
    first, seen = _check(monkeypatch, "dev", "999.0")
    assert first.fresh and len(seen) == 1
    # a second forced check within 5 minutes reuses the result without a request
    second = asyncio.run(updates.check(force=True))
    assert second is first and not second.fresh and 0 < second.to_dict()["retry_in"] <= updates.MANUAL_COOLDOWN
    assert len(seen) == 1
    # after the cooldown a forced check fetches again
    first.checked_at -= updates.MANUAL_COOLDOWN + 1
    third = asyncio.run(updates.check(force=True))
    assert third.fresh and len(seen) == 2 and third.to_dict()["retry_in"] > 0
