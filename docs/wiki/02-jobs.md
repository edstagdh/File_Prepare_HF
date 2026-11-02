# Jobs, preview and the queue

A **job** processes the `.mp4` files directly inside one working directory, using its own copy of the
settings (taken from the defaults when the job is created and editable until it runs).

## Lifecycle

| State                          | Meaning                                                                     |
|--------------------------------|-----------------------------------------------------------------------------|
| draft                          | Settings saved, no valid preview yet.                                       |
| previewing                     | Dry run in progress - nothing on disk is changed.                           |
| ready                          | Preview passed: the job can be queued.                                      |
| blocked                        | Preview found blocking issues - change files or settings and preview again. |
| queued                         | Waiting for a free slot.                                                    |
| running                        | Processing files.                                                           |
| stopping                       | Finishing the current file, then stopping.                                  |
| stopped / interrupted          | Can be resumed: already finished files are skipped.                         |
| completed / failed / cancelled | Finished.                                                                   |

## Preview (dry run)

The preview runs the whole matching phase (TPDB lookups, your approvals, manual input) and computes every
new file name, folder, output file and step **without touching any file**. It reports:

- **errors** (block the job): duplicate target names, a target name already used by another file,
  paths that are too long, matches without title/site/performers, invalid strict-mode names, invalid or
  incompatible settings, missing credentials, missing ffmpeg encoders, another queued/running job on the same
  directory, TPDB not reachable...
- **warnings / info**: files with **no TPDB match** (skipped by the run), name fallbacks (too long), missing
  metadata, existing outputs you will be asked about, files that are already HEVC, folders that will be reused...

### Changing names after the preview

The new names come from the TPDB data. Without running the preview again you can:

- **Suffix for all files** (box above the table, also in the job's **Naming** tab as *Suffix for all files*): added
  after every name and to the video title, e.g. `VR2Normal` -> `Studio.25.01.01.Jane.Doe.VR2Normal.mp4`. It is
  combined with a suffix detected from the original name (`.vr2normal.` etc.). Empty removes it.
- **File name mode per file** (list under the file name): pick *title*, *names*, *names + title* or *TPDB id* for
  that file only - each option shows the name it gives - e.g. *title* when a scene has so many performers that the
  name gets too long. *Job default* goes back to the job's **File name mode**. The usual fallbacks apply when the
  chosen name is longer than the maximum (a `NAME_FALLBACK` warning). Saved per job in
  `naming.filename_mode_overrides`. Not available while a custom name is set, nor for previews made before 14.15
  (run the preview again).
- **Rename** on a file row: type the new name (without `.mp4`); it is used exactly as typed, the suffix is not added
  again, and the row shows a *custom* badge. An empty name goes back to the name from TPDB.

All of these update the table at once and re-check conflicts (duplicate or existing names, path length) - the job stays
*ready*, or becomes *blocked* if the new names conflict. Cover, thumbnails, previews and the sub folder follow the
new name. Changing any other setting still needs a new preview.

Each file row also has two actions that require a new preview afterwards:

- **Set TPDB UUID** - search the scene manually on theporndb.net and paste its UUID; the file is then looked up
  directly by that UUID (stored per job in `matching.uuid_overrides`).
- **Move out** - moves the file to the moved-out folder (`input.excluded_folder`, default `_excluded` inside the
  working directory; sub folders are never scanned) so it is no longer part of the job.

### TPDB failures

- **No match** (TPDB answered but found nothing, or nothing matched the date / your selection): a warning in the
  preview; the run skips the file, marks it *skipped* and sends a notification listing the skipped files.
- **Connection failure** (timeouts, network errors, server errors or rate limiting after the automatic retries,
  and you answered *no* to "Retry again?") or a rejected API token: the preview stops with a blocking
  `TPDB_CONNECTION` error, and a running job stops as *failed* (Resume continues with the remaining files).

Fix the files / settings and run the preview again. A clean preview is required before queueing (configurable
in **Settings > Application > Jobs**). Your preview answers are stored and re-used by the run, so you are not asked
twice. If the input files change after the preview, the run refuses to start and asks for a new preview.

### Running without a preview

The preview is required by default because it catches name collisions and other problems before any file is
renamed or re-encoded. Turn off **Settings > Application > Jobs > Require a clean preview** to get a
**Start without preview** button: the run then checks the files first (asking the same questions) and stops as
*blocked*, before changing anything, if a blocking issue is found.

### Performer images

When performer portraits or the upload template are enabled, the job page lists every performer of the preview
with **Performer images**, missing ones first. For each one you can:

- **Find faces on TPDB** - downloads the performer's TPDB posters and crops the faces;
- **Link to TPDB performer** - for performers without a TPDB id (e.g. entered manually): paste the performer's UUID from
  their TPDB page; it is checked against the API, remembered in `data/performer_tpdb_links.json`, and enables
  *Find faces on TPDB*;
- **Upload an image...** - your own JPG/PNG/WebP/AVIF; faces are cropped from it too, or use it as-is;
- **Paste URL** - an image that is already hosted.

**Use this face** uploads the image to the first working host of **Image hosts > Portrait hosts** and saves the URL
in `Resources/Performers_Images.json` (previous version kept in `Resources/.backups`); the other posters and faces of
that performer are then deleted and the performer collapses to its image with a **Replace image** button. The template
uses these images. The run itself never creates portraits (no `faces` folder in the working directory) - performers
still without an image are listed in the preview.

## Settings of a job

Each job keeps its own copy of the settings. The job page shows them read-only under **Settings used by this
job** (only the tabs that apply, e.g. Video preview only when previews are enabled); **Edit settings** changes
them and requires a new preview.

## Questions during a job (user input)

Every place where the old terminal script waited for typed input now asks in the browser:

- **During the preview:** TPDB scene selection, date / title / performer approval, full manual metadata (lists such
  as performers or tags are one form with a field per entry and **+ Add another**; Enter adds the next field),
  the user-input file name, "retry the API?".
- **During the run:** audio track selection, keep or regenerate an existing cover / thumbnail sheet / preview,
  cut point confirmation, 2FA and "did you submit the tracker form?".

The job waits at that step and its page shows a yellow card with the question, the recent log lines (they list
the options, e.g. the numbered scene candidates) and quick-answer buttons (yes/no, keep/regenerate...); free
text is always possible. Several questions can be open at once (e.g. a tracker confirmation while the next file
is being processed).

You are told wherever you are in the app: a sticky **"Input needed"** toast links to the job, the sidebar shows
how many questions are waiting (it stays 30 seconds; status toasts 10 s, errors 20 s), links printed in the log
or the question are clickable, the browser shows a desktop notification when the tab is in the background (allow
it once), and with notifications enabled you get a Telegram message. Job state changes (ready, blocked, running,
completed, failed...) also show a short toast with a link to the job.

## Start, stop, cancel

- **Queue / start** - start a ready job; **Resume** continues a stopped/interrupted/failed job.
- **Stop after current file** - graceful; remaining files are kept for a later resume.
- **Cancel** - immediate; the running step (e.g. ffmpeg encoding) is killed and its temporary file removed. When
  the cancel interrupts a file that already changed something (renamed, outputs created, images uploaded) - or
  files still waiting for their tracker upload - the job asks once **Undo?** before it ends: *undo* deletes the
  files and Pixhost uploads created for those files, lists Hamster / ImgBB / imgbox images to delete by hand and
  renames / moves the videos back (the same undo as for a cancelled tracker upload, see
  [Image hosts and tracker](04-image-hosts-and-tracker.md)); *keep* leaves them. Files that finished before the
  cancel are never touched. The job's tracker browser is closed.
- **Duplicate** - new draft with the same settings.

## Scheduling rules

- At most **Max concurrent jobs** run at the same time (default 3).
- At most **one** running job that re-encodes on the **CPU** (x265 / SVT-AV1 use ~70% of the CPU) and at most
  **one** that re-encodes on the **GPU** (consumer GPUs limit parallel encode sessions). Both limits are in
  **Settings > Application > Jobs**. Other jobs keep starting while an encoding job waits.
- Two jobs never run on the same working directory. A parent folder and a folder inside it can be processed at
  the same time (a job only reads the `.mp4` files directly inside its own folder), unless a file of one job would
  get a sub folder that is the other job's folder (or one of its output folders) - the preview reports that as
  `OUTPUT_DIRECTORY_IN_USE`.
