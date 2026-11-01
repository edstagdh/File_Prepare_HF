"""
File Prepare HF - command line entry point.

    python run.py web [--host H] [--port P]   start the web interface (server only; normally use main.py)
    python run.py process [--yes]             preview + process the configured working directory in the terminal
    python run.py preview                     dry-run only: show what would happen
    python run.py migrate-config [--overwrite] convert pre-14.0 config files to the new format
    python run.py create-user NAME [--role R] create a web user (admin/operator/viewer)
    python run.py check-ffmpeg                show the ffmpeg/ffprobe report
    python run.py sync-resources              sync Resources files from GitHub
    python run.py pixhost list|delete <url>   list or delete Pixhost uploads
"""

import argparse
import asyncio
import getpass
import os
import sys

from fileprep import __version__, paths
from fileprep.log import setup_logging


def main(argv=None) -> int:
    os.chdir(paths.PROJECT_ROOT)  # legacy relative paths (Logs/, Resources/) resolve from the project root
    parser = argparse.ArgumentParser(prog="run.py", description=f"File Prepare HF {__version__}")
    sub = parser.add_subparsers(dest="command")
    web = sub.add_parser("web", help="start the web interface")
    web.add_argument("--host")
    web.add_argument("--port", type=int)
    proc = sub.add_parser("process", help="preview and process in the terminal")
    proc.add_argument("--yes", action="store_true", help="do not ask for confirmation after a clean preview")
    sub.add_parser("preview", help="dry run")
    mig = sub.add_parser("migrate-config", help="convert legacy config files")
    mig.add_argument("--overwrite", action="store_true")
    user = sub.add_parser("create-user", help="create a web user")
    user.add_argument("username")
    user.add_argument("--role", default="admin", choices=["admin", "operator", "viewer"])
    sub.add_parser("check-ffmpeg")
    sub.add_parser("sync-resources")
    pix = sub.add_parser("pixhost")
    pix.add_argument("action", choices=["list", "delete"])
    pix.add_argument("manage_url", nargs="?")
    args = parser.parse_args(argv)
    command = args.command or "web"

    from fileprep.config.store import store
    paths.ensure_runtime_dirs()
    if command == "migrate-config":
        setup_logging()
        for line in store.migrate_legacy(overwrite=args.overwrite):
            print(line)
        return 0
    store.bootstrap()
    app_config = store.app()
    setup_logging(app_config.server.log_level)

    if command == "web":
        import uvicorn
        from fileprep.web.app import create_app
        uvicorn.run(create_app(), host=args.host or app_config.server.host, port=args.port or app_config.server.port,
                    log_level=app_config.server.log_level.lower(), proxy_headers=True)
        return 0
    if command == "create-user":
        from fileprep.config.models import AuthUser
        from fileprep.web.security import hash_password
        if any(u.username == args.username for u in app_config.auth.users):
            print("User already exists")
            return 1
        password = getpass.getpass("Password (10+ characters): ")
        if len(password) < 10 or password != getpass.getpass("Repeat: "):
            print("Password too short or not matching")
            return 1
        app_config.auth.users.append(AuthUser(username=args.username, password_hash=hash_password(password), role=args.role))
        store.save("app", app_config)
        print(f"User {args.username} ({args.role}) created")
        return 0
    if command == "check-ffmpeg":
        from fileprep.system.ffmpeg import check_ffmpeg
        report = asyncio.run(check_ffmpeg(force=True))
        for tool in (report.ffmpeg, report.ffprobe):
            print(f"{tool.name}: {'OK' if tool.ok else 'FAIL'} version={tool.version} date={tool.build_date} path={tool.path}")
        print("encoders:", ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in report.encoders.items()))
        for label, link in report.update_links:
            print(f"update: {label}: {link}")
        return 0 if report.ok else 1
    if command == "sync-resources":
        from fileprep.system.resource_sync import sync_all
        token = store.credentials().github.token if app_config.resource_sync.github_token_from_credentials else None
        for result in asyncio.run(sync_all(app_config.resource_sync.entries, token or None)):
            print(result)
        return 0
    if command == "pixhost":
        from fileprep.uploaders.pixhost import pixhost_delete_image, pixhost_pending_uploads
        if args.action == "list":
            for rec in pixhost_pending_uploads():
                print(f"{rec['timestamp']}  {rec['key']}\n  view:   {rec['show_url']}\n  delete: {rec['manage_url']}")
            return 0
        if not args.manage_url:
            print("delete needs a manage_url")
            return 2
        return 0 if asyncio.run(pixhost_delete_image(args.manage_url)) else 1
    if command in ("process", "preview"):
        from fileprep.cli import run_terminal
        return asyncio.run(run_terminal(preview_only=command == "preview", assume_yes=getattr(args, "yes", False)))
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
