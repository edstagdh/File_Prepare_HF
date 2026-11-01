"""
File Prepare HF - web application entry point.

    python main.py               start the web interface and open it in the browser
    python main.py --no-browser  start the server only (or turn off Settings > Application > Web server > Open the browser)

Since 14.0 this file starts the web application (it replaced the 13.x terminal script).
Terminal / maintenance commands (preview, process, migrate-config, create-user...) are in run.py.
Host and port: Settings > Application > Web server (Configs/app.json).
"""

import socket
import sys
import threading
import webbrowser

REQUIRED = ("fastapi", "uvicorn", "jinja2", "pydantic", "loguru", "httpx")


def missing_packages() -> list[str]:
    import importlib.util
    return [name for name in REQUIRED if importlib.util.find_spec(name) is None]


def lan_addresses() -> list[str]:
    """IPv4 addresses of this machine (shown when the server listens on all interfaces)."""
    addresses = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))  # no traffic is sent; picks the default interface
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    try:
        addresses.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return sorted(a for a in addresses if not a.startswith("127."))


def banner(messages: list[str]) -> str:
    import asyncio
    from fileprep import __version__, paths
    from fileprep.config.store import store
    from fileprep.system.ffmpeg import check_ffmpeg

    app = store.app()
    server, base = app.server, app.server.base_path.rstrip("/")
    local = f"http://127.0.0.1:{server.port}{base}/" if server.host in ("0.0.0.0", "::", "127.0.0.1", "localhost") \
        else f"http://{server.host}:{server.port}{base}/"
    lines = ["=" * 72, f"  File Prepare HF {__version__} - web application", "=" * 72,
             f"  Open in your browser:  {local}"]
    if server.host in ("0.0.0.0", "::"):
        lines += [f"  On your network:       http://{ip}:{server.port}{base}/" for ip in lan_addresses()]
    else:
        lines.append(f"  Listening on:          {server.host}:{server.port} (local only - change in Settings > Application)")
    if not app.auth.users:
        lines.append("  First start:           open the address above to create the administrator account")
    try:
        report = asyncio.run(check_ffmpeg(force=True))
        ff = f"ffmpeg {report.ffmpeg.version or '?'} / ffprobe {report.ffprobe.version or '?'}" if report.ok else \
            "ffmpeg/ffprobe MISSING or too old - see Tools > ffmpeg"
    except Exception as exc:  # never block start-up on the check
        ff = f"ffmpeg check failed: {exc}"
    lines += [f"  ffmpeg:                {ff}",
              f"  Settings:              {paths.CONFIG_DIR}",
              f"  Jobs and job logs:     {paths.JOBS_DIR}",
              f"  Application log:       {paths.LOGS_DIR}",
              "  Guide:                 Wiki tab in the app (docs/wiki)"]
    lines += [f"  Note:                  {m}" for m in messages if m.startswith("Migrated")]
    lines += ["  Stop the server with Ctrl+C", "=" * 72]
    return "\n".join(lines)


def open_browser(url: str, command: str) -> None:
    """Open ``url`` with the configured browser command, or the system default browser."""
    if not command.strip():
        webbrowser.open(url)
        return
    import shlex
    import subprocess
    args = shlex.split(command, posix=(sys.platform != "win32"))
    args = [a.strip('"').replace("{url}", url) for a in args]
    if not any(url in a for a in args):
        args.append(url)
    try:
        subprocess.Popen(args)
    except OSError as exc:
        print(f"Could not start the browser command ({exc}) - open {url} manually.")


def main() -> int:
    missing = missing_packages()
    if missing:
        print(f"Missing packages: {', '.join(missing)}\nInstall them with:  pip install -r requirements.txt")
        return 1

    import run
    from fileprep import paths
    from fileprep.config.store import store

    paths.ensure_runtime_dirs()
    messages = store.bootstrap()
    print(banner(messages), flush=True)
    server = store.app().server
    host = "127.0.0.1" if server.host in ("0.0.0.0", "::") else server.host
    if server.open_browser and "--no-browser" not in sys.argv:
        url = f"http://{host}:{server.port}{server.base_path.rstrip('/')}/"
        threading.Timer(2.0, open_browser, args=(url, server.browser_command)).start()
    return run.main(["web"])


if __name__ == "__main__":
    sys.exit(main())
