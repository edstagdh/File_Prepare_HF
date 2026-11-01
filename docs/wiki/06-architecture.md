# Architecture (developers)

```
main.py                    starts the web application (prints the URLs, opens the browser)
run.py                     CLI commands (web / process / preview / migrate-config / create-user / ...)
fileprep/
  config/                  pydantic models (models.py), legacy migration, thread-safe JSON store
  runtime.py               per-job context: settings snapshot, prompt(), progress, stop checks
  pipeline/                naming (pure rules), matching, validation, planner (dry run), runner, manual input
  jobs/                    job model + manager (thread per job, scheduler, prompts, logs, persistence)
  media/                   probe, metadata, audio, encode (HEVC), cover, portrait, thumbnail_sheet, video_preview, mediainfo
  tpdb/                    TPDB API client, scene selection/review, performers, collection
  uploaders/               hamster, pixhost, imgbb, imgbox + service.py (fallback / URL lookup)
  tracker/                 session.py (persistent Selenium thread per tracker), upload.py, torrent.py
  notifiers/               registry + telegram
  system/                  ffmpeg checks, GPU detection (gpu.py), update check (updates.py), resource sync, Python checks
  template/                BBCode template and tags
  web/                     FastAPI app, auth/CSRF, generic forms, modules tab, wiki renderer, templates, static UI
docs/wiki/                 these pages (rendered in the Wiki tab)
data/                      runtime state: data/jobs/<id>.json + .log (git ignored)
```

## Key ideas

- **Thread per job.** Each preview/run gets its own thread and asyncio loop, so CPU-heavy steps never block the
  web server or other jobs. Cancel = `task.cancel()`; subprocess helpers kill ffmpeg on cancellation.
- **Prompts.** Code anywhere in the pipeline calls `await fileprep.runtime.prompt(text)`. Inside a job the
  `JobPrompter` publishes the question to the UI and waits for the answer; in the terminal it reads stdin.
- **Plan then run.** `planner.build_plan()` resolves matches and names without side effects; `runner.run_plan()`
  executes the stored plan, so preview answers are re-used.
- **Settings drive the UI.** Forms are generated from the pydantic models (`label`, `help`, `visible_if`,
  `choices_help` in `json_schema_extra`). Add a field to a model and it shows up in Settings and the job form.

## Extending

- **Setting:** add a field with `setting(default, "Label", "help", visible_if=...)` to the right section model,
  read it from `runtime.config()` (or the `job` argument) and, if it replaces an old key, map it in `config/legacy.py`.
- **Image host:** add `uploaders/<host>.py` with `async <host>_upload_single_image(path, base_name, label) -> bool`
  that appends `{"<base> - <label>": [{"image_url": ...}]}` to `<base>_<host>.txt`, then register it in
  `uploaders/service.py` (`HOST_INFO`, `_upload`) and the `ImageHost` literal in `config/models.py`.
- **Notifier:** add `notifiers/<name>.py` with `async send_notification(message) -> bool` and register it in
  `notifiers/__init__.py` and the `NotificationSettings.notifier` literal.
- **Tracker:** add the site to `tracker.json > sites`, the account to credentials and the name to the
  `TrackerUploadSettings.trackers` literal.

## Tests

`python -m pytest tests` - naming rules, legacy migration, validation, scheduler (HEVC gate), web helpers.
