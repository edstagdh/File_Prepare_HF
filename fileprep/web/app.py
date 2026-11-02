"""
FastAPI application: authentication, the single-page UI shell and the JSON API used by it.

Roles: ``admin`` (everything, settings and users), ``operator`` (jobs and tools), ``viewer`` (read only).
Every state-changing API call needs the session's CSRF token in the ``X-CSRF-Token`` header.
"""

import asyncio
import os
import platform
import string
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from pydantic import BaseModel, ValidationError
from starlette.middleware.sessions import SessionMiddleware

from fileprep import __version__, paths
from fileprep.config.models import AuthUser, JobConfig, ResourceSyncEntry
from fileprep.config.store import CONFIG_FILES, ConfigError, store
from fileprep.jobs.manager import JobError, manager
from fileprep.web import forms, markdown
from fileprep.web.security import hash_password, new_token, throttle, verify_password

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
ROLE_LEVEL = {"viewer": 0, "operator": 1, "admin": 2}


# =============================================================================== app factory
def ensure_session_secret() -> str:
    app_config = store.app()
    if not app_config.auth.session_secret:
        app_config.auth.session_secret = new_token()
        store.save("app", app_config)
    return app_config.auth.session_secret


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.start()
    sync = store.app().resource_sync
    if sync.auto_sync_on_start and sync.entries:
        asyncio.create_task(_sync_resources(None))
    from fileprep.log import asyncio_exception_handler
    asyncio.get_running_loop().set_exception_handler(asyncio_exception_handler)
    update_task = asyncio.create_task(_update_loop())
    logger.info(f"File Prepare HF {__version__} web interface ready")
    yield
    update_task.cancel()
    manager.shutdown()


async def _update_loop():
    """Version check on start and every CACHE_SECONDS while running (Settings > Updates)."""
    from fileprep.system import updates
    while True:
        settings = store.app().updates
        if settings.channel != "off" and settings.check_on_start:
            try:
                await updates.check(force=True)
            except Exception as exc:  # never let the check affect the server
                logger.warning(f"Version check failed: {exc}")
        await asyncio.sleep(updates.CACHE_SECONDS)


def create_app() -> FastAPI:
    paths.ensure_runtime_dirs()
    store.bootstrap()
    app_config = store.app()
    app = FastAPI(title="File Prepare HF", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None, root_path=app_config.server.base_path)
    app.add_middleware(SessionMiddleware, secret_key=ensure_session_secret(), session_cookie="fileprep_session",
                       max_age=app_config.auth.session_hours * 3600, same_site="lax", https_only=app_config.auth.secure_cookies)
    @app.middleware("http")
    async def one_line_errors(request: Request, call_next):
        # an unexpected error in a request is logged as one line (traceback only in Logs/) instead of the
        # server's multi-line "Exception in ASGI application"
        try:
            return await call_next(request)
        except Exception as exc:
            from fileprep.errors import describe
            logger.opt(exception=exc).error(f"{request.method} {request.url.path} failed")
            return JSONResponse({"detail": describe(exc)}, status_code=500)

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    _register_routes(app)
    return app


# =============================================================================== auth helpers
def _base(request: Request) -> str:
    return request.scope.get("root_path", "").rstrip("/")


def _users() -> list[AuthUser]:
    return store.app().auth.users


def current_user(request: Request) -> Optional[dict]:
    username = request.session.get("user")
    if not username:
        return None
    user = next((u for u in _users() if u.username == username), None)
    if not user or request.session.get("pwd") != user.password_hash[-12:]:
        request.session.clear()  # deleted user or changed password -> log out
        return None
    return {"username": user.username, "role": user.role}


def require(role: str = "viewer"):
    def dependency(request: Request) -> dict:
        user = current_user(request)
        if not user:
            raise HTTPException(401, "Not logged in")
        if ROLE_LEVEL[user["role"]] < ROLE_LEVEL[role]:
            raise HTTPException(403, f"Requires the {role} role")
        if request.method not in ("GET", "HEAD") and request.headers.get("x-csrf-token") != request.session.get("csrf"):
            raise HTTPException(403, "Invalid CSRF token - reload the page")
        return user
    return dependency


def _error(exc: Exception, status: int = 400) -> JSONResponse:
    if isinstance(exc, ValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse({"detail": "Invalid settings", "errors": details}, status_code=422)
    return JSONResponse({"detail": str(exc)}, status_code=status)


class JobBody(BaseModel):
    name: str = ""
    config: dict[str, Any]
    save_as_defaults: bool = False


class AnswerBody(BaseModel):
    answer: str = ""


class UserBody(BaseModel):
    username: str
    password: str
    role: str = "operator"


class PasswordBody(BaseModel):
    current_password: str = ""
    new_password: str


# =============================================================================== routes
def _register_routes(app: FastAPI) -> None:  # noqa: C901 - flat route table
    # ------------------------------------------------------------------ pages
    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if not _users():
            return RedirectResponse(f"{_base(request)}/setup", 303)
        request.session.setdefault("csrf", new_token())
        return templates.TemplateResponse(request, "login.html", {"base": _base(request), "csrf": request.session["csrf"],
                                                                  "error": None, "version": __version__})

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request):
        form = await request.form()
        username, password = str(form.get("username", "")).strip(), str(form.get("password", ""))
        auth = store.app().auth
        key = (username.lower(), request.client.host if request.client else "?")
        ctx = {"base": _base(request), "csrf": request.session.get("csrf", ""), "version": __version__}
        if form.get("csrf") != request.session.get("csrf"):
            return templates.TemplateResponse(request, "login.html", {**ctx, "error": "Session expired, try again."}, status_code=400)
        locked = throttle.locked_for(key, auth.max_failed_logins, auth.lockout_minutes)
        if locked:
            return templates.TemplateResponse(request, "login.html", {**ctx, "error": f"Too many attempts. Try again in {locked // 60 + 1} min."},
                                              status_code=429)
        user = next((u for u in auth.users if u.username.lower() == username.lower()), None)
        if not user or not verify_password(password, user.password_hash):
            throttle.failed(key)
            logger.warning(f"Failed login for '{username}' from {key[1]}")
            return templates.TemplateResponse(request, "login.html", {**ctx, "error": "Invalid username or password."}, status_code=401)
        throttle.succeeded(key)
        request.session.clear()
        request.session.update({"user": user.username, "pwd": user.password_hash[-12:], "csrf": new_token()})
        logger.info(f"User '{user.username}' logged in from {key[1]}")
        return RedirectResponse(f"{_base(request)}/", 303)

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse(f"{_base(request)}/login", 303)

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request):
        if _users():
            return RedirectResponse(f"{_base(request)}/login", 303)
        request.session.setdefault("csrf", new_token())
        return templates.TemplateResponse(request, "setup.html", {"base": _base(request), "csrf": request.session["csrf"],
                                                                  "error": None, "version": __version__})

    @app.post("/setup", response_class=HTMLResponse)
    async def setup(request: Request):
        if _users():
            return RedirectResponse(f"{_base(request)}/login", 303)
        form = await request.form()
        ctx = {"base": _base(request), "csrf": request.session.get("csrf", ""), "version": __version__}
        username, password = str(form.get("username", "")).strip(), str(form.get("password", ""))
        error = _validate_new_user(username, password, str(form.get("password2", "")))
        if form.get("csrf") != request.session.get("csrf"):
            error = "Session expired, try again."
        if error:
            return templates.TemplateResponse(request, "setup.html", {**ctx, "error": error}, status_code=400)
        app_config = store.app()
        app_config.auth.users.append(AuthUser(username=username, password_hash=hash_password(password), role="admin"))
        store.save("app", app_config)
        logger.info(f"Administrator '{username}' created")
        return RedirectResponse(f"{_base(request)}/login", 303)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        user = current_user(request)
        if not user:
            return RedirectResponse(f"{_base(request)}/login", 303)
        request.session.setdefault("csrf", new_token())
        return templates.TemplateResponse(request, "app.html", {"base": _base(request), "csrf": request.session["csrf"],
                                                                "user": user, "version": __version__,
                                                                "repo_url": f"https://github.com/{store.app().updates.repository}"})

    # ------------------------------------------------------------------ session / forms
    @app.get("/api/me")
    async def me(user=Depends(require())):
        return {**user, "version": __version__, "os": platform.system(), "require_preview": store.app().jobs.require_preview}

    @app.get("/api/about")
    async def about(user=Depends(require())):
        import sys
        from urllib.parse import urlencode
        from fileprep.system.ffmpeg import check_ffmpeg
        updates = store.app().updates
        repo_url = f"https://github.com/{updates.repository}"
        ffmpeg = (await check_ffmpeg()).ffmpeg
        python = platform.python_version()
        environment = {"Version": __version__, "Update channel": updates.channel, "OS": f"{platform.system()} {platform.release()}",
                       "Python": python, "ffmpeg": ffmpeg.version or ("not found" if not ffmpeg.found else "?")}
        body = ("**What happened?**\n\n\n**What did you expect?**\n\n\n**Steps to reproduce**\n1. \n\n"
                "**Log lines** (job log or Logs/)\n```\n\n```\n\n**Environment**\n"
                + "\n".join(f"- {k}: {v}" for k, v in environment.items()))
        return {"version": __version__, "repository": updates.repository, "repository_url": repo_url,
                "issues_url": f"{repo_url}/issues", "new_issue_url": f"{repo_url}/issues/new?" + urlencode({"body": body}),
                "branches": {"stable": f"{repo_url}/tree/{updates.stable_branch}", "dev": f"{repo_url}/tree/{updates.dev_branch}"},
                "license_url": f"{repo_url}/blob/{updates.dev_branch if updates.channel == 'dev' else updates.stable_branch}/LICENSE",
                "environment": {**environment, "Python executable": sys.executable, "Project folder": str(paths.PROJECT_ROOT),
                                "Settings": str(paths.CONFIG_DIR), "Data": str(paths.DATA_DIR), "Logs": str(paths.LOGS_DIR)}}

    @app.get("/api/forms/job")
    async def job_form(user=Depends(require())):
        return {"sections": forms.job_form()}

    @app.get("/api/job-defaults")
    async def job_defaults(user=Depends(require())):
        return store.job_config().model_dump(mode="json")

    @app.get("/api/settings/{name}")
    async def get_settings(name: str, user=Depends(require())):
        if name not in CONFIG_FILES:
            raise HTTPException(404)
        if name == "credentials" and user["role"] != "admin":
            raise HTTPException(403, "Requires the admin role")
        model = store.load(name)
        if name == "credentials":  # one account entry per defined tracker site
            from fileprep.config.models import TrackerAccount
            for site in store.load("tracker").sites:
                model.trackers.setdefault(site, TrackerAccount())
        data, secret_flags = forms.mask_secrets(model)
        if name == "app":
            data["auth"].pop("users", None)
        return {"name": name, "file": f"Configs/{CONFIG_FILES[name][0]}", "values": data, "secrets": secret_flags,
                "sections": forms.settings_form(name, store.model_type(name))}

    @app.put("/api/settings/{name}")
    async def put_settings(name: str, request: Request, user=Depends(require("admin"))):
        if name not in CONFIG_FILES:
            raise HTTPException(404)
        body = await request.json()
        try:
            current = store.load(name)
            body = forms.merge_secrets(body, current)
            if name == "app":  # users are managed on the Users tab
                body.setdefault("auth", {})["users"] = [u.model_dump() for u in current.auth.users]
            store.save(name, body)
        except (ValidationError, ConfigError) as exc:
            return _error(exc)
        logger.info(f"Settings '{name}' saved by {user['username']}")
        return {"ok": True, "restart_required": name == "app"}

    # ------------------------------------------------------------------ jobs
    @app.get("/api/jobs")
    async def list_jobs(user=Depends(require())):
        return {"jobs": [j.summary() for j in manager.list_jobs()], "scheduler": manager.scheduler_state()}

    @app.post("/api/jobs")
    async def create_job(body: JobBody, user=Depends(require("operator"))):
        try:
            config = JobConfig.model_validate(body.config)
        except ValidationError as exc:
            return _error(exc)
        if body.save_as_defaults:
            if user["role"] != "admin":
                raise HTTPException(403, "Only admins can change the default settings")
            store.save_job_defaults(config)
        return manager.create(config, body.name, user["username"]).summary()

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str, user=Depends(require())):
        try:
            data = manager.snapshot(job_id)
        except KeyError:
            raise HTTPException(404, "Job not found")
        data["summary"] = manager.get(job_id).summary()
        data["plan_is_current"] = manager.get(job_id).plan_is_current
        return data

    @app.put("/api/jobs/{job_id}")
    async def update_job(job_id: str, body: JobBody, user=Depends(require("operator"))):
        try:
            config = JobConfig.model_validate(body.config)
            if body.save_as_defaults:
                if user["role"] != "admin":
                    raise HTTPException(403, "Only admins can change the default settings")
                store.save_job_defaults(config)
            return manager.update_config(job_id, config, body.name).summary()
        except ValidationError as exc:
            return _error(exc)
        except (JobError, KeyError) as exc:
            return _error(exc, 409)

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str, user=Depends(require("operator"))):
        try:
            manager.delete(job_id)
        except (JobError, KeyError) as exc:
            return _error(exc, 409)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/{action}")
    async def job_action(job_id: str, action: str, request: Request, user=Depends(require("operator"))):
        actions = {"preview": manager.start_preview, "queue": manager.enqueue, "start": manager.enqueue,
                   "stop": manager.stop, "cancel": manager.cancel}
        try:
            if action == "duplicate":
                source = manager.get(job_id)
                return manager.create(source.config.model_copy(deep=True), f"{source.name} (copy)", user["username"]).summary()
            if action == "move-out":
                body = await request.json()
                return {"moved_to": manager.move_out(job_id, body.get("file", ""))}
            if action == "names":
                body = await request.json()
                return manager.set_names(job_id, suffix=body.get("suffix"), file_name=body.get("file"),
                                         new_name=body.get("name"), mode=body.get("mode")).summary()
            if action == "uuid":
                body = await request.json()
                return manager.set_uuid(job_id, body.get("file", ""), body.get("uuid", "")).summary()
            if action not in actions:
                raise HTTPException(404, "Unknown action")
            logger.info(f"Job {job_id}: {action} by {user['username']}")
            return actions[action](job_id).summary()
        except (JobError, KeyError) as exc:
            return _error(exc, 409)

    # ------------------------------------------------------------------ performer images (before the run)
    @app.get("/api/jobs/{job_id}/performers")
    async def job_performers(job_id: str, user=Depends(require())):
        from fileprep.media.portrait_picker import performers_of
        try:
            return {"performers": performers_of(manager.get(job_id)),
                    "hosts": manager.get(job_id).config.processing.image_hosts.portrait_hosts}
        except KeyError:
            raise HTTPException(404, "Job not found")

    class _DeclinePrompter:
        """Web requests cannot answer questions: "Retry again?" after failed API retries is answered "no"."""
        async def ask(self, question):
            return "n"

    @app.post("/api/jobs/{job_id}/performers/{action}")
    async def performer_action(job_id: str, action: str, request: Request, user=Depends(require("operator"))):
        from fileprep.runtime import Runtime, use_runtime
        try:
            job = manager.get(job_id)
        except KeyError:
            raise HTTPException(404, "Job not found")
        with use_runtime(Runtime(config=job.config, prompter=_DeclinePrompter(), job_id=job_id)):
            return await _performer_action(job, job_id, action, request)

    async def _performer_action(job, job_id: str, action: str, request: Request):
        from fileprep.media import portrait_picker as picker
        from fileprep.tpdb import TpdbConnectionError
        try:
            if action == "upload":
                form = await request.form()
                upload = form.get("file")
                if upload is None or not hasattr(upload, "read"):
                    raise ValueError("No file received")
                return {"candidates": await picker.add_upload(job_id, str(form.get("key", "")), upload.filename or "image.jpg",
                                                              await upload.read(), job.config)}
            body = await request.json()
            key = picker.performer_key(str(body.get("key", "")))
            if not key:
                raise ValueError("Missing performer")
            if action == "link":
                return await picker.link_tpdb(key, str(body.get("uuid", "")))
            if action == "discard":
                picker.clear_candidates(job_id, key)
                return {"ok": True}
            if action == "candidates":
                return {"candidates": await picker.generate_candidates(job_id, key, body.get("tpdb_id"), job.config)}
            if action == "choose":
                return {"url": await picker.choose(job_id, key, str(body.get("file", "")), job.config)}
            if action == "url":
                url = str(body.get("url", "")).strip()
                if not url.startswith(("https://", "http://")):
                    raise ValueError("Enter an http(s) image URL")
                picker.save_url(key, url)
                picker.clear_candidates(job_id, key)
                return {"url": url}
        except (ValueError, FileNotFoundError, TpdbConnectionError) as exc:
            return _error(exc)
        raise HTTPException(404, "Unknown action")

    @app.get("/api/jobs/{job_id}/performers/image/{key}/{file_name}")
    async def performer_image(job_id: str, key: str, file_name: str, user=Depends(require())):
        from fastapi.responses import FileResponse
        from fileprep.media.portrait_picker import candidate_path
        try:
            return FileResponse(candidate_path(job_id, key, file_name), headers={"Cache-Control": "no-store"})
        except FileNotFoundError:
            raise HTTPException(404, "Image not found")

    @app.post("/api/jobs/{job_id}/prompts/{prompt_id}")
    async def answer(job_id: str, prompt_id: str, body: AnswerBody, user=Depends(require("operator"))):
        try:
            manager.answer_prompt(job_id, prompt_id, body.answer)
        except (JobError, KeyError) as exc:
            return _error(exc, 409)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/logs")
    async def job_logs(job_id: str, after: int = 0, user=Depends(require())):
        lines, next_index = manager.logs(job_id, after)
        return {"lines": lines, "next": next_index}

    # ------------------------------------------------------------------ tools
    @app.get("/api/updates")
    async def update_status(refresh: bool = False, user=Depends(require())):
        from fileprep.system import updates
        return (await updates.check(force=refresh)).to_dict()

    @app.get("/api/tools/ffmpeg")
    async def ffmpeg_status(refresh: bool = False, user=Depends(require())):
        from fileprep.system.ffmpeg import check_ffmpeg
        return (await check_ffmpeg(force=refresh)).to_dict()

    @app.get("/api/tools/gpu")
    async def gpu_status(refresh: bool = False, user=Depends(require())):
        from fileprep.system.gpu import detect
        return (await detect(force=refresh)).to_dict()

    @app.get("/api/tools/resources")
    async def resources(user=Depends(require())):
        from fileprep.system.resource_sync import sync_state
        sync = store.app().resource_sync
        files = sorted(p.name for p in paths.RESOURCES_DIR.iterdir() if p.is_file())
        return {"entries": [e.model_dump() for e in sync.entries], "state": sync_state(), "files": files,
                "auto_sync_on_start": sync.auto_sync_on_start}

    @app.put("/api/tools/resources/entries")
    async def save_resource_entries(request: Request, user=Depends(require("admin"))):
        from fileprep.system.resource_sync import SyncError, validate_entry
        try:
            entries = [ResourceSyncEntry.model_validate(e) for e in await request.json()]
            for entry in entries:
                validate_entry(entry)
        except (ValidationError, SyncError) as exc:
            return _error(exc)
        app_config = store.app()
        app_config.resource_sync.entries = entries
        store.save("app", app_config)
        return {"ok": True}

    @app.post("/api/tools/resources/{action}")
    async def resource_action(action: str, request: Request, user=Depends(require("operator"))):
        body = await request.json() if (await request.body()) else {}
        name = body.get("name")
        if action == "sync":
            return {"results": await _sync_resources(name)}
        if action == "check":
            from fileprep.system.resource_sync import check_entry
            token = _github_token()
            results = []
            for entry in store.app().resource_sync.entries:
                if name and entry.name != name:
                    continue
                try:
                    results.append(await check_entry(entry, token))
                except Exception as exc:
                    results.append({"name": entry.name, "status": "error", "error": str(exc)})
            return {"results": results}
        raise HTTPException(404)

    @app.get("/api/tools/tracker-sessions")
    async def tracker_sessions(user=Depends(require())):
        from fileprep.tracker.session import registry
        return {"sessions": registry.status()}

    @app.post("/api/tools/tracker-sessions/{sid}/close")
    async def close_tracker(sid: str, user=Depends(require("operator"))):
        from fileprep.tracker.session import registry
        return {"ok": registry.close(sid)}

    @app.get("/api/tools/pixhost")
    async def pixhost_links(user=Depends(require("operator"))):
        from fileprep.uploaders.pixhost import pixhost_pending_uploads
        return {"uploads": pixhost_pending_uploads()}

    @app.post("/api/tools/pixhost/delete")
    async def pixhost_delete(request: Request, user=Depends(require("operator"))):
        from fileprep.uploaders.pixhost import pixhost_delete_image
        body = await request.json()
        return {"ok": await pixhost_delete_image(body.get("manage_url", ""))}

    # ------------------------------------------------------------------ resources
    @app.get("/api/resources")
    async def resource_files(user=Depends(require())):
        kinds = {".otf": "font", ".ttf": "font", ".woff": "font", ".woff2": "font", ".txt": "template", ".json": "json",
                 ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".avif": "image", ".gif": "image", ".onnx": "model",
                 ".jsonl": "log"}
        files = []
        for p in sorted(paths.RESOURCES_DIR.iterdir(), key=lambda x: x.name.lower()):
            if p.is_file() and not p.name.startswith("."):
                st = p.stat()
                files.append({"name": p.name, "size": st.st_size, "modified": st.st_mtime, "kind": kinds.get(p.suffix.lower(), "other")})
        o = store.processing().outputs
        return {"files": files, "template_file": o.template_file, "font_file": o.font_file}

    def _resource_path(name: str) -> Path:
        path = (paths.RESOURCES_DIR / name).resolve()
        if path.parent != paths.RESOURCES_DIR.resolve() or not path.is_file() or name.startswith("."):
            raise HTTPException(404, "File not found")
        return path

    @app.get("/api/resources/file/{name}")
    async def resource_file(name: str, user=Depends(require())):
        from fastapi.responses import FileResponse
        path = _resource_path(name)
        if path.suffix.lower() == ".jsonl" and ROLE_LEVEL[user["role"]] < ROLE_LEVEL["operator"]:
            raise HTTPException(403, "Requires the operator role")  # Pixhost delete links
        return FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
                            media_type="text/plain; charset=utf-8" if path.suffix.lower() in (".txt", ".json", ".jsonl") else None)

    @app.get("/api/resources/template-preview/{name}")
    async def template_preview(name: str, user=Depends(require())):
        from fileprep.template.preview import render_preview
        _resource_path(name)
        return render_preview(name)

    @app.get("/api/fs/list")
    async def fs_list(path: str = "", user=Depends(require("operator"))):
        return _list_directory(path)

    # ------------------------------------------------------------------ modules
    @app.get("/api/modules")
    async def modules(user=Depends(require())):
        from fileprep.web.modules import module_status
        return {"modules": await module_status()}

    @app.post("/api/modules/{name}/test")
    async def test_module(name: str, user=Depends(require("operator"))):
        from fileprep.web.modules import test_module as run_test
        return await run_test(name)

    # ------------------------------------------------------------------ wiki
    @app.get("/api/wiki")
    async def wiki_index(user=Depends(require())):
        pages = []
        for page in sorted(paths.WIKI_DIR.glob("*.md")):
            first = page.read_text(encoding="utf-8").splitlines()[0] if page.stat().st_size else page.stem
            pages.append({"page": page.stem, "title": first.lstrip("# ").strip() or page.stem})
        return {"pages": pages}

    @app.get("/api/wiki/{page}")
    async def wiki_page(page: str, user=Depends(require())):
        if not all(c in string.ascii_letters + string.digits + "-_" for c in page):
            raise HTTPException(404)
        path = paths.WIKI_DIR / f"{page}.md"
        if not path.is_file():
            raise HTTPException(404, "Page not found")
        return {"page": page, "html": markdown.render(path.read_text(encoding="utf-8"))}

    # ------------------------------------------------------------------ users
    @app.get("/api/users")
    async def users(user=Depends(require("admin"))):
        return {"users": [{"username": u.username, "role": u.role} for u in _users()]}

    @app.post("/api/users")
    async def add_user(body: UserBody, user=Depends(require("admin"))):
        if body.role not in ROLE_LEVEL:
            return _error(ValueError("Invalid role"))
        if any(u.username.lower() == body.username.lower() for u in _users()):
            return _error(ValueError("User already exists"), 409)
        error = _validate_new_user(body.username, body.password, body.password)
        if error:
            return _error(ValueError(error))
        app_config = store.app()
        app_config.auth.users.append(AuthUser(username=body.username.strip(), password_hash=hash_password(body.password), role=body.role))
        store.save("app", app_config)
        return {"ok": True}

    @app.delete("/api/users/{username}")
    async def delete_user(username: str, user=Depends(require("admin"))):
        app_config = store.app()
        remaining = [u for u in app_config.auth.users if u.username != username]
        if not any(u.role == "admin" for u in remaining):
            return _error(ValueError("At least one admin must remain"), 409)
        app_config.auth.users = remaining
        store.save("app", app_config)
        return {"ok": True}

    @app.post("/api/users/{username}/password")
    async def change_password(username: str, body: PasswordBody, request: Request, user=Depends(require())):
        is_self = username == user["username"]
        if not is_self and user["role"] != "admin":
            raise HTTPException(403)
        app_config = store.app()
        target = next((u for u in app_config.auth.users if u.username == username), None)
        if not target:
            raise HTTPException(404)
        if is_self and not verify_password(body.current_password, target.password_hash):
            return _error(ValueError("Current password is incorrect"), 403)
        error = _validate_new_user(username, body.new_password, body.new_password)
        if error:
            return _error(ValueError(error))
        target.password_hash = hash_password(body.new_password)
        store.save("app", app_config)
        if is_self:
            request.session["pwd"] = target.password_hash[-12:]
        return {"ok": True}


# =============================================================================== helpers
def _validate_new_user(username: str, password: str, password2: str) -> Optional[str]:
    if not username or len(username) > 64 or not all(c.isalnum() or c in "._-" for c in username):
        return "Username: 1-64 letters, digits, '.', '_' or '-'."
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if password != password2:
        return "Passwords do not match."
    return None


def _github_token() -> Optional[str]:
    if store.app().resource_sync.github_token_from_credentials:
        return store.credentials().github.token or None
    return None


async def _sync_resources(name: Optional[str]) -> list[dict]:
    from fileprep.system.resource_sync import sync_all
    entries = [e for e in store.app().resource_sync.entries if not name or e.name == name]
    return await sync_all(entries, _github_token())


def _list_directory(path: str) -> dict:
    if not path:
        if os.name == "nt":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return {"path": "", "parent": None, "dirs": drives, "mp4_count": 0}
        path = str(Path.home())
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise HTTPException(404, "Directory not found")
    try:
        entries = list(os.scandir(path))
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    dirs = sorted((e.name for e in entries if e.is_dir() and not e.name.startswith(".")), key=str.lower)
    mp4 = sum(1 for e in entries if e.is_file() and e.name.lower().endswith(".mp4"))
    parent = os.path.dirname(path) if os.path.dirname(path) != path else ("" if os.name == "nt" else None)
    return {"path": path, "parent": parent, "dirs": dirs, "mp4_count": mp4}
