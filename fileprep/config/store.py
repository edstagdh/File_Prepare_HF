"""
Loading, validating and saving the JSON settings files in ``Configs/``.

The store is thread-safe (jobs run in their own threads), caches parsed models and reloads a file when
its modification time changes, writes atomically and keeps rolling backups in ``Configs/backups``.
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from fileprep import paths
from fileprep.config import legacy
from fileprep.config.models import (AppConfig, Credentials, JobConfig, ProcessingSettings, ThumbnailSettings,
                                    TrackerConfig, VideoPreviewSettings)

M = TypeVar("M", bound=BaseModel)

CONFIG_FILES: dict[str, tuple[str, type[BaseModel]]] = {
    "processing": ("processing.json", ProcessingSettings),
    "thumbnails": ("thumbnails.json", ThumbnailSettings),
    "video_preview": ("video_preview.json", VideoPreviewSettings),
    "tracker": ("tracker.json", TrackerConfig),
    "app": ("app.json", AppConfig),
    "credentials": ("credentials.secret", Credentials),
}
_MIGRATORS = {
    "processing": legacy.migrate_processing,
    "thumbnails": legacy.migrate_thumbnails,
    "video_preview": legacy.migrate_video_preview,
    "tracker": legacy.migrate_tracker,
    "credentials": legacy.migrate_credentials,
}
MAX_BACKUPS = 10


class ConfigError(Exception):
    pass


class ConfigStore:
    def __init__(self, config_dir: Path = paths.CONFIG_DIR, legacy_root: Path = paths.PROJECT_ROOT):
        self.config_dir = Path(config_dir)
        self.legacy_root = Path(legacy_root)
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, BaseModel]] = {}

    # ---------------------------------------------------------------- paths
    def path(self, name: str) -> Path:
        return self.config_dir / CONFIG_FILES[name][0]

    def model_type(self, name: str) -> type[BaseModel]:
        return CONFIG_FILES[name][1]

    # ---------------------------------------------------------------- bootstrap / migration
    def bootstrap(self) -> list[str]:
        """Create every missing settings file, migrating legacy files when present. Returns messages."""
        messages = []
        self.config_dir.mkdir(parents=True, exist_ok=True)
        legacy_creds = self._read_legacy("credentials")
        for name in CONFIG_FILES:
            if self.path(name).exists():
                continue
            old = self._read_legacy(name) if name in _MIGRATORS else None
            if old is not None:
                model = (_MIGRATORS[name](old, legacy_creds) if name == "processing" else _MIGRATORS[name](old))
                messages.append(f"Migrated {legacy.LEGACY_FILES[name]} -> Configs/{CONFIG_FILES[name][0]}")
            else:
                model = self.model_type(name)()
                messages.append(f"Created default Configs/{CONFIG_FILES[name][0]}")
            self.save(name, model, backup=False)
        for message in messages:
            logger.info(message)
        return messages

    def migrate_legacy(self, overwrite: bool = False) -> list[str]:
        """Explicit migration (CLI ``migrate-config``). Existing new files are kept unless ``overwrite``."""
        messages = []
        legacy_creds = self._read_legacy("credentials")
        for name, migrator in _MIGRATORS.items():
            old = self._read_legacy(name)
            if old is None:
                messages.append(f"skip {name}: {legacy.LEGACY_FILES[name]} not found")
                continue
            if self.path(name).exists() and not overwrite:
                messages.append(f"skip {name}: Configs/{CONFIG_FILES[name][0]} exists (use --overwrite)")
                continue
            model = migrator(old, legacy_creds) if name == "processing" else migrator(old)
            self.save(name, model)
            messages.append(f"migrated {legacy.LEGACY_FILES[name]} -> Configs/{CONFIG_FILES[name][0]}")
        return messages

    def _read_legacy(self, name: str) -> dict | None:
        rel = legacy.LEGACY_FILES.get(name)
        if not rel:
            return None
        path = self.legacy_root / rel
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error(f"Cannot read legacy file {path}: {exc}")
            return None

    # ---------------------------------------------------------------- load / save
    def load(self, name: str) -> BaseModel:
        path = self.path(name)
        with self._lock:
            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                model = self.model_type(name)()
                self._cache[name] = (0.0, model)
                return model.model_copy(deep=True)
            cached = self._cache.get(name)
            if cached and cached[0] == mtime:
                return cached[1].model_copy(deep=True)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                model = self.model_type(name).model_validate(raw)
            except (json.JSONDecodeError, ValidationError) as exc:
                raise ConfigError(f"Invalid {path.name}: {exc}") from exc
            self._cache[name] = (mtime, model)
            return model.model_copy(deep=True)

    def save(self, name: str, model: BaseModel | dict, backup: bool = True) -> BaseModel:
        model_type = self.model_type(name)
        if isinstance(model, dict):
            model = model_type.model_validate(model)
        path = self.path(name)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if backup and path.exists():
                self._backup(path)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, path)
            if name == "credentials" and os.name != "nt":
                os.chmod(path, 0o600)
            self._cache[name] = (path.stat().st_mtime, model)
        return model

    def _backup(self, path: Path) -> None:
        backup_dir = self.config_dir / "backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (backup_dir / f"{path.name}.{stamp}").write_bytes(path.read_bytes())
        old = sorted(backup_dir.glob(f"{path.name}.*"))
        for stale in old[:-MAX_BACKUPS]:
            stale.unlink(missing_ok=True)

    # ---------------------------------------------------------------- typed accessors
    def processing(self) -> ProcessingSettings:
        return self.load("processing")  # type: ignore[return-value]

    def app(self) -> AppConfig:
        return self.load("app")  # type: ignore[return-value]

    def credentials(self) -> Credentials:
        return self.load("credentials")  # type: ignore[return-value]

    def job_config(self) -> JobConfig:
        """Snapshot of the current defaults for a new job."""
        return JobConfig(processing=self.load("processing"), thumbnails=self.load("thumbnails"),
                         video_preview=self.load("video_preview"), tracker=self.load("tracker"))

    def save_job_defaults(self, job_config: JobConfig) -> None:
        self.save("processing", job_config.processing)
        self.save("thumbnails", job_config.thumbnails)
        self.save("video_preview", job_config.video_preview)
        self.save("tracker", job_config.tracker)


store = ConfigStore()


def get_credentials() -> Credentials:
    """Current credentials (re-read when the file changes)."""
    return store.credentials()
