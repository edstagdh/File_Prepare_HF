"""Modules tab: what each integration does, whether it is configured, and a connectivity test."""

import httpx

from fileprep import paths
from fileprep.config.store import store
from fileprep.uploaders.service import HOST_INFO, missing_credentials

MODULES = [
    ("tpdb", "TPDB (ThePornDB)", "matching", "Scene/performer metadata, collection."),
    ("ffmpeg", "ffmpeg / ffprobe", "media", "Probing, re-encoding, thumbnails and previews."),
    ("thumbnails", "Thumbnails sheet", "media", "Static contact sheet with optional file info header."),
    ("video_preview", "Video preview", "media", "Animated WebP/AVIF/WebM/GIF previews and preview sheets."),
    ("portraits", "Performer portraits", "media", "Face crops (YuNet) for performers without a mapped image."),
    ("template", "Upload template", "output", "BBCode template and tags from Resources templates."),
    ("hamster", "Hamster image host", "image host", "API upload (all image formats, animated previews)."),
    ("pixhost", "Pixhost image host", "image host", "Anonymous API upload, delete links kept in Resources."),
    ("imgbb", "ImgBB image host", "image host", "Browser-based upload (Chrome)."),
    ("imgbox", "imgbox image host", "image host", "Anonymous upload, JPG/PNG only."),
    ("tracker", "Tracker upload", "tracker", "Persistent logged-in browser session, torrent creation, dupe check."),
    ("telegram", "Telegram notifier", "notifier", "Messages when input is needed or a job finishes."),
    ("github", "Resource sync", "resources", "Keeps Resources files in sync with GitHub."),
]


async def module_status() -> list[dict]:
    """Each module is "ready" (configured and on by default), "off" (works, not enabled by default) or
    "setup" (something it needs is missing - the status text says what)."""
    creds = store.credentials()
    processing = store.processing()
    o, hosts, tracker = processing.outputs, processing.image_hosts, processing.tracker
    app = store.app()

    def state(missing: str, enabled: bool, ok_text: str, off_text: str = "configured, enable it per job when needed"):
        if missing:
            return "setup", missing
        return ("ready", ok_text) if enabled else ("off", off_text)

    accounts = [n for n, a in creds.trackers.items() if a.username and a.password and a.announce_url]
    sites = store.load("tracker").sites
    status = {
        "tpdb": state("" if creds.tpdb.api_token else "TPDB API token missing (Settings > Credentials) - only full manual mode works",
                      processing.matching.matching_mode != "full_manual", "API token set"),
        "ffmpeg": ("ready", "see Tools > ffmpeg for versions and encoders"),
        "thumbnails": state("", o.thumbnails_sheet, "enabled in the default job settings"),
        "video_preview": state("", o.video_preview, "enabled in the default job settings"),
        "portraits": state("" if paths.FACE_DETECTION_MODEL.is_file() else "face detection model missing in Resources/",
                           o.performer_portraits, "enabled in the default job settings"),
        "template": state("" if paths.resource(o.template_file).is_file() else f"template file {o.template_file} not found in Resources/",
                          o.upload_template, f"using {o.template_file}"),
        "tracker": state("" if accounts else "no tracker account with username, password and announce URL (Settings > Credentials)",
                         tracker.enabled, "accounts: " + ", ".join(accounts), f"accounts: {', '.join(accounts)} - sites: {', '.join(sites)}"),
        "telegram": state("" if creds.notifiers.telegram_bot_token and creds.notifiers.telegram_chat_id
                          else "bot token / chat id missing (Settings > Credentials)", processing.notifications.enabled, "bot configured"),
        "github": state("", bool(app.resource_sync.entries), f"{len(app.resource_sync.entries)} synced resource(s)",
                        "no resources configured (Tools > Resource sync)"),
    }
    for host in HOST_INFO:
        missing = missing_credentials(host)
        used = [a for a, lst in (("cover", hosts.cover_hosts), ("thumbnails", hosts.thumbnails_hosts),
                                 ("previews", hosts.preview_hosts)) if host in lst]
        status[host] = state(("credentials missing: " + ", ".join(missing)) if missing else "", bool(used),
                             "used for " + ", ".join(used), "configured, not in any default host list")
    return [{"name": name, "label": label, "kind": kind, "description": desc, "state": status[name][0], "ok": status[name][0] != "setup",
             "status": status[name][1], "testable": name in ("tpdb", "telegram", "hamster", "pixhost", "ffmpeg")}
            for name, label, kind, desc in MODULES]


async def test_module(name: str) -> dict:
    creds = store.credentials()
    try:
        if name == "tpdb":
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(creds.tpdb.scenes_api_url, params={"q": "test", "per_page": 1},
                                     headers={"Authorization": f"Bearer {creds.tpdb.api_token}", "Accept": "application/json"})
            return {"ok": r.status_code == 200, "message": f"HTTP {r.status_code}"}
        if name == "telegram":
            from fileprep.notifiers.telegram import send_notification
            ok = await send_notification("File Prepare HF test notification")
            return {"ok": ok, "message": "sent" if ok else "failed - see the log"}
        if name == "hamster":
            url = creds.image_hosts.hamster_site_url.rstrip("/")
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(url)
            missing = missing_credentials("hamster")
            return {"ok": r.status_code < 500 and not missing,
                    "message": f"site HTTP {r.status_code}" + (f", missing: {', '.join(missing)}" if missing else "")}
        if name == "pixhost":
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("https://pixhost.to")
            return {"ok": r.status_code < 500, "message": f"HTTP {r.status_code}"}
        if name == "ffmpeg":
            from fileprep.system.ffmpeg import check_ffmpeg
            report = await check_ffmpeg(force=True)
            return {"ok": report.ok, "message": f"ffmpeg {report.ffmpeg.version or '?'} / ffprobe {report.ffprobe.version or '?'}"}
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    return {"ok": False, "message": "No test available for this module"}
