from fileprep.config.models import Credentials, JobConfig
from fileprep.pipeline.validation import validate_job_config
from fileprep.system.ffmpeg import FfmpegReport, ToolStatus


def ok_ffmpeg():
    tool = ToolStatus(name="ffmpeg", command="ffmpeg", found=True, ok=True)
    return FfmpegReport(os_name="Linux", ffmpeg=tool, ffprobe=tool, encoders={"libx265": True, "libwebp": True, "libvpx-vp9": True})


def codes(job, creds=None):
    return {i.code for i in validate_job_config(job, creds or Credentials(), ok_ffmpeg(), [3, 0], [3, 99]) if i.level == "error"}


def test_tracker_prerequisites(tmp_path):
    job = JobConfig()
    job.processing.input.working_directory = str(tmp_path)
    job.processing.tracker.enabled = True
    errors = codes(job)
    assert {"E40_TRACKER_SUBFOLDER", "E42_TRACKER_TEMPLATE", "E43_TRACKER_PREVIEW_HOST", "TRACKER_CREDENTIALS", "TPDB_TOKEN"} <= errors


def test_host_fallback_credentials(tmp_path):
    job = JobConfig()
    p = job.processing
    p.input.working_directory = str(tmp_path)
    p.matching.matching_mode = "full_manual"
    p.outputs.cover_image = True
    p.image_hosts.mode = "fallback"
    p.image_hosts.cover_hosts = ["hamster", "pixhost"]  # hamster lacks credentials, pixhost needs none
    assert "HOST_CREDENTIALS" not in codes(job)
    p.image_hosts.cover_hosts = ["hamster"]
    assert "HOST_NONE_USABLE" in codes(job)


def test_exclusive_and_preview_grid(tmp_path):
    job = JobConfig()
    job.processing.input.working_directory = str(tmp_path)
    job.processing.matching.matching_mode = "full_manual"
    job.processing.metadata.set_exclusive = True
    job.processing.outputs.video_preview = True
    job.video_preview.segment_count = 13
    errors = codes(job)
    assert "E49_EXCLUSIVE" in errors and "PREVIEW_GRID" in errors
