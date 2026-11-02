"""Data model of a job plan (the result of a preview / dry-run) and of per-file results."""

import hashlib
import json
import os
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

IssueLevel = Literal["error", "warning", "info"]


class PlanIssue(BaseModel):
    level: IssueLevel
    code: str
    message: str
    file: Optional[str] = None


class SceneMatch(BaseModel):
    """Metadata resolved for one file (TPDB match or full manual input)."""
    scene_title: Optional[str] = None
    performers: Optional[Any] = None  # [[name, tpdb_id], ...] (or "Invalid")
    image_url: Optional[str] = None
    slug: Optional[str] = None
    scene_url: Optional[str] = None
    tpdb_image_url: Optional[str] = None
    site: Optional[str] = None
    studio: Optional[str] = None
    description: Optional[str] = None
    date: str = "0000-00-00"
    tags: Optional[list[str]] = None
    tpdb_uuid: Optional[str] = None
    tpdb_id: Optional[Any] = None
    markers: Optional[list[Any]] = None
    manual_suffix: str = ""
    full_manual: bool = False

    @classmethod
    def from_api_tuple(cls, result: tuple) -> "SceneMatch":
        (title, performers, image_url, slug, scene_url, tpdb_image_url, site, studio, description, date, tags, uuid,
         tpdb_id, markers) = result
        return cls(scene_title=title, performers=[list(p) for p in performers] if isinstance(performers, list) else performers,
                   image_url=image_url, slug=slug, scene_url=scene_url, tpdb_image_url=tpdb_image_url, site=site,
                   studio=studio, description=description, date=date or "0000-00-00", tags=tags, tpdb_uuid=uuid,
                   tpdb_id=tpdb_id, markers=markers)


class PlannedOutput(BaseModel):
    kind: str
    path: str
    exists: bool = False
    note: str = ""


class FilePlan(BaseModel):
    source_name: str
    source_size: int
    source_mtime: float
    status: Literal["ok", "warning", "error", "skipped"] = "ok"
    match: Optional[SceneMatch] = None
    flags: dict[str, bool] = Field(default_factory=dict)
    suffix: str = ""  # suffix after the name (flag suffix + job suffix, e.g. VR2Normal)
    flag_suffix: str = ""  # suffix detected from the original name's flags
    auto_base_name: str = ""  # base name built from the TPDB data (before a per-file name)
    custom_name: bool = False  # new name set by the user for this file
    new_base_name: str = ""  # without suffix / extension
    new_file_name: str = ""  # with suffix and extension
    title: str = ""
    studio_info: str = ""
    studio_tags: list[str] = Field(default_factory=list)
    scene_pretty_date: str = ""
    tpdb_url: Optional[str] = None
    output_directory: str = ""
    steps: list[str] = Field(default_factory=list)
    outputs: list[PlannedOutput] = Field(default_factory=list)
    issues: list[PlanIssue] = Field(default_factory=list)


class JobPlan(BaseModel):
    created_at: float
    working_directory: str
    config_hash: str
    inputs_hash: str
    files: list[FilePlan] = Field(default_factory=list)
    issues: list[PlanIssue] = Field(default_factory=list)  # job level issues
    renames: list[tuple[str, str]] = Field(default_factory=list)  # strict-mode pre-processing renames
    encode_slot: Optional[str] = None  # "cpu" / "gpu" when the job re-encodes videos

    @property
    def all_issues(self) -> list[PlanIssue]:
        return self.issues + [issue for f in self.files for issue in f.issues]

    @property
    def blocking(self) -> bool:
        return any(issue.level == "error" for issue in self.all_issues)

    def summary(self) -> dict:
        issues = self.all_issues
        return {"files": len(self.files), "errors": sum(i.level == "error" for i in issues),
                "warnings": sum(i.level == "warning" for i in issues), "blocking": self.blocking}


class FileResult(BaseModel):
    source_name: str
    final_path: Optional[str] = None
    status: Literal["pending", "running", "success", "failed", "skipped"] = "pending"
    error: Optional[str] = None
    completed_steps: list[str] = Field(default_factory=list)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    interrupted: bool = False  # the job was cancelled while this file was processed / awaited its upload


def config_hash(config_dump: dict) -> str:
    return hashlib.sha256(json.dumps(config_dump, sort_keys=True).encode()).hexdigest()[:16]


def inputs_hash(directory: str, names: list[str]) -> str:
    """Fingerprint of the input files (name, size, mtime) - a changed input invalidates the preview."""
    entries = []
    for name in sorted(names):
        try:
            st = os.stat(os.path.join(directory, name))
            entries.append((name, st.st_size, int(st.st_mtime)))
        except FileNotFoundError:
            entries.append((name, -1, 0))
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()[:16]
