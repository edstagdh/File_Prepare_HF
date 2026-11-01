"""Job state model (persisted as data/jobs/<id>.json)."""

import time
from typing import Literal, Optional

from pydantic import BaseModel, Field

from fileprep.config.models import JobConfig
from fileprep.pipeline.plan import FileResult, JobPlan

JobState = Literal["draft", "previewing", "ready", "blocked", "queued", "running", "stopping",
"completed", "failed", "cancelled", "stopped", "interrupted"]

ACTIVE_STATES = {"previewing", "queued", "running", "stopping"}
RUNNING_STATES = {"previewing", "running", "stopping"}
EDITABLE_STATES = {"draft", "ready", "blocked", "completed", "failed", "cancelled", "stopped", "interrupted"}
RESUMABLE_STATES = {"stopped", "interrupted", "failed"}

STATE_HELP = {
    "draft": "Settings saved - run a preview.",
    "previewing": "Preview in progress (answer questions below if asked).",
    "ready": "Preview passed - the job can be queued.",
    "blocked": "Preview found blocking issues - change the files or settings and preview again.",
    "queued": "Waiting for a free slot (HEVC jobs run one at a time).",
    "running": "Processing files.",
    "stopping": "Stopping after the current file.",
    "completed": "Finished.",
    "failed": "Finished with failed files or an error.",
    "cancelled": "Cancelled.",
    "stopped": "Stopped - start it again to continue with the remaining files.",
    "interrupted": "The server stopped while this job was active - start it again to continue.",
}


class JobProgress(BaseModel):
    stage: str = ""
    percent: Optional[float] = None
    detail: str = ""
    updated_at: float = 0.0


class PendingPrompt(BaseModel):
    id: str
    title: str
    text: str
    choices: list[str] = Field(default_factory=list)
    multiline: bool = False
    kind: str = "text"
    item_label: str = ""
    context: list[str] = Field(default_factory=list)
    asked_at: float = Field(default_factory=time.time)


class Job(BaseModel):
    id: str
    name: str
    created_by: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    state: JobState = "draft"
    config: JobConfig
    plan: Optional[JobPlan] = None
    results: dict[str, FileResult] = Field(default_factory=dict)
    progress: JobProgress = Field(default_factory=JobProgress)
    prompts: list[PendingPrompt] = Field(default_factory=list)
    error: Optional[str] = None
    queued_at: Optional[float] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    activity: Literal["", "preview", "run"] = ""

    @property
    def encode_slot(self) -> Optional[str]:
        """"cpu" / "gpu" when the job re-encodes videos (limits: jobs.max_cpu_encode_jobs / max_gpu_encode_jobs)."""
        e = self.config.processing.encoding
        return e.device if e.re_encode else None

    @property
    def encode_label(self) -> str:
        e = self.config.processing.encoding
        return f"{e.codec.upper()} · {e.device.upper()}" if e.re_encode else ""

    @property
    def working_directory(self) -> str:
        return self.config.processing.input.working_directory

    @property
    def plan_is_current(self) -> bool:
        from fileprep.pipeline.plan import config_hash
        return bool(self.plan) and self.plan.config_hash == config_hash(self.config.model_dump(mode="json"))

    def summary(self) -> dict:
        counts = {status: 0 for status in ("success", "failed", "skipped", "running", "pending")}
        for result in self.results.values():
            counts[result.status] = counts.get(result.status, 0) + 1
        return {"id": self.id, "name": self.name, "state": self.state, "state_help": STATE_HELP[self.state],
                "encode_slot": self.encode_slot, "encode_label": self.encode_label, "directory": self.working_directory, "created_at": self.created_at,
                "started_at": self.started_at, "finished_at": self.finished_at, "progress": self.progress.model_dump(),
                "prompts": len(self.prompts), "files": len(self.plan.files) if self.plan else 0, "results": counts,
                "plan": self.plan.summary() if self.plan else None, "error": self.error, "activity": self.activity,
                "created_by": self.created_by}
