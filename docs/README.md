# File Prepare HF

**File Prepare HF** prepares adult video releases for upload to HF and similar trackers. It matches each video with
[ThePornDB](https://theporndb.net) (TPDB), renames it, optionally re-encodes it to HEVC or AV1 (CPU or GPU), writes
metadata and chapters, creates a cover, a thumbnails sheet and animated previews (WebP, AVIF, WebM, GIF), keeps
performer images, builds the BBCode upload template, uploads the images to image hosts, and fills the tracker upload
form in a browser that stays logged in.

Everything runs from a password-protected **web interface** that you start with `python main.py`. Work is organised
in **jobs**. Each job runs a **preview (dry run)** first, which changes nothing on disk. The job then waits in a
**queue** and runs. You can follow its live progress and logs, and you answer its questions in the browser.

> Current version: see `VERSION` (shown in the app's sidebar). Development happens on the `dev` branch; stable
> releases are on `master`.

---

## Contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Install and start](#install-and-start)
4. [Workflow](#workflow)
5. [Settings](#settings)
6. [Image formats and hosts](#image-formats-and-hosts)
7. [Versions and updates](#versions-and-updates)
8. [Command line](#command-line)
9. [Files and folders](#files-and-folders)
10. [Documentation](#documentation)
11. [Development](#development)

---

## Features

**Jobs and safety**
- **Job queue**: create, preview, queue, start, stop after the current file, cancel immediately, resume and
  duplicate jobs. Each job keeps its own copy of the settings, shown read-only on the job page.
- **Mandatory preview (dry run)**: it resolves every TPDB match, new name, folder and output file without touching
  any file. Duplicate or existing target names, paths that are too long, invalid settings, missing credentials or
  encoders, TPDB connection failures and overlapping jobs **block** the job. Files with no match are skipped by
  the run. You can turn the preview requirement off.
- **Fix names after the preview**: add a suffix to all files (for example `VR2Normal`) or rename a single file
  without running the preview again. You can also set a TPDB UUID by hand or move a file out of the job.
- **Scheduler**: a limit on concurrent jobs, at most one CPU-encoding job and one GPU-encoding job at a time, and
  never two jobs on the same folder (a parent and a child folder can run side by side).
- **Crash-safe**: job state is saved after every file. Interrupted jobs can be resumed, and temporary or partial
  files are removed after failures.

**Media processing**
- **TPDB matching**: OSHash, file-name parser, strict name format or fully manual entry. It also supports scenes,
  JAV and movies, review steps for performers, date and title, and chapter markers.
- **Naming**: build names from the title, the performers, both, or the TPDB id. Date formats, length limits, an
  optional sub folder per file and suffix detection (`vr2normal`, `bts`, `upscaled`…) are configurable.
- **Encoding**: HEVC or AV1 on the CPU (x265, SVT-AV1 or libaom) or the GPU (NVENC, Quick Sync, AMF, VAAPI,
  VideoToolbox). A failed GPU encode can fall back to the CPU. You can downscale above 1080p and cap the CPU usage.
- **GPU detection**: the app tests each backend on this machine, and also uses the GPU to speed up thumbnails and
  preview segments.
- **Outputs**: cover, thumbnails sheet, animated previews and preview sheets (WebP / **AVIF** / WebM / GIF, at
  480p, 720p or 1080p), MediaInfo file, BBCode template and tags.
- **Performer images**: before the run you can pick a face cropped from TPDB posters, upload your own image or
  paste a URL. Performers entered manually can be linked to TPDB by UUID.

**Uploads**
- **Image hosts**: Hamster, Pixhost, ImgBB and imgbox, with an ordered list per asset. In *fallback* mode the
  first host that works is used; in *all* mode every host gets the file. File formats and size limits are checked
  before uploading.
- **Tracker upload**: each job gets its own Selenium browser per tracker that stays logged in across the job's
  files and closes when the job ends; a cancelled upload resets the form for the next file (2FA is supported). Form selectors are set per site. Torrent creation and the duplicate
  check are built in. You check
  the filled form yourself before submitting.
- **Notifications**: Telegram messages and in-app toasts with links to the job. Questions show an "Input needed"
  toast and a desktop notification.

**Administration**
- **Settings UI**: built from typed models, with an ⓘ tooltip on every setting and a backup on every save. Old
  13.x config files are migrated automatically.
- **Tools**: version and update check, ffmpeg/ffprobe check with update links for your OS, GPU detection,
  resource sync from GitHub and Pixhost delete links.
- **Resources page**: preview templates rendered with sample data, fonts, JSON maps and images.
- **Users**: admin, operator and viewer roles, scrypt password hashes, CSRF protection and a login lockout.
- **Built-in wiki**: the pages in `docs/wiki/`, rendered in the *Wiki / Guide* tab.
- **About page and issue reporting**: version and update status, links to the GitHub repository, branches and
  license, and the environment (OS, Python, ffmpeg). *Report an issue* (on the About page and at the bottom of the
  sidebar) opens a new GitHub issue pre-filled with a template and that environment.

---

## Requirements

| Requirement            | Notes                                                                                                                                                          |
|------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Python 3.10 – 3.14     | Range configurable in *Settings › Application › Environment*                                                                                                   |
| ffmpeg + ffprobe       | Recent build with `libx265`, `libwebp`, `libvpx-vp9` and `libsvtav1` (or `libaom-av1`, which is needed for AV1 encoding and AVIF previews). Checked in *Tools* |
| TPDB API token         | Not needed in full manual mode                                                                                                                                 |
| Firefox or Chrome      | Only for tracker uploads and ImgBB                                                                                                                             |
| GPU drivers (optional) | NVIDIA / Intel / AMD / Apple / VAAPI for GPU encoding and decoding                                                                                             |

Python packages are listed in `requirements.txt` (FastAPI, Uvicorn, Pydantic 2, Pillow ≥ 11.3 for AVIF, OpenCV,
Selenium, httpx, torf…).

---

## Install and start

```bash
git clone -b dev https://github.com/edstagdh/File_Prepare_HF.git      # or -b master for the stable channel
cd File_Prepare_HF
python -m venv venv
source venv/bin/activate                                                # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

`main.py` prints the version, the app's URLs (including your LAN addresses when it listens on all interfaces),
the ffmpeg status and where settings and logs are stored. It then opens `http://127.0.0.1:8765` in your browser.

- `python main.py --no-browser` skips opening the browser. To make that permanent, turn off *Settings ›
  Application › Web server › Open the browser*. To use a specific browser or profile, set *Browser command*
  (for example `firefox -P Work -new-tab {url}`).
- **First visit:** create the administrator account. The settings files in `Configs/` are created
  automatically, and 13.x files are migrated.
- **Remote access:** set the bind address to `0.0.0.0`, put an HTTPS reverse proxy in front of the app, and turn
  on *Secure cookies*.
- **Updating:** run `git pull` on your branch, then `pip install -r requirements.txt`, then restart.

---

## Workflow

### 1. One-time setup

1. **Settings › Credentials**: enter the TPDB token, image host accounts, tracker accounts, Telegram and an
   optional GitHub token.
2. **Settings › Processing defaults**: set the default job settings (matching, naming, encoding, outputs, hosts,
   tracker).
3. **Tools**: check ffmpeg and GPU detection. You can also sync `Resources/` from GitHub.
4. **Settings › Tracker sites**: set the login and upload URLs, category, 2FA and form selectors for each tracker.

### 2. A job

```
New job ─► Save & preview ─► (answer questions) ─► ready ─► Queue / start ─► running ─► completed
                 │                                   │                │
                 └──► blocked (fix files/settings) ◄─┘        stop / cancel / resume
```

1. **New job**: choose the working directory. The job processes the `.mp4` files directly inside it. Adjust the
   job's settings in the tabs; the Thumbnails, Video preview and Tracker tabs appear only when those features
   are on.
2. **Preview**: TPDB lookups and your review answers (scene choice, performers, date, title, manual data)
   produce a plan table. It shows each file's new name, folder, outputs and steps, plus errors, warnings and
   info.
    - **Errors** block the job. Fix them and preview again, or for a single file use *Set TPDB UUID* or
      *Move out*.
   - Change names without running the preview again: use **Suffix for all files**, choose another **file name mode for one file** (e.g. the title instead of a long list of
     performers), or **Rename** a row.
3. **Performer images** (when portraits or the template are on): choose or upload a face for each performer.
   The face is uploaded to the portrait hosts and saved in `Resources/Performers_Images.json`.
4. **Queue / start**: the scheduler starts the job when a slot is free. The run reuses your answers from the
   preview.
5. **Answer questions while it runs**: audio track, keep or regenerate an existing cover, sheet or preview, cut
   points, 2FA, tracker confirmation. A yellow card on the job page shows the recent log lines and quick
   answers.
6. **Results**: renamed files, optionally in a sub folder per file, next to the generated images, template and
   tags. Image host URLs are saved in `<name>_<host>.txt`.

### 3. What happens to each file (run)

| #  | Step                                                               | Enabled by                                           |
|----|--------------------------------------------------------------------|------------------------------------------------------|
| 1  | Create the output (sub) folder, rename the video                   | *Naming*                                             |
| 2  | Choose the audio track                                             | *Encoding › Keep a single audio track*               |
| 3  | Re-encode to HEVC/AV1 on CPU/GPU (already HEVC/AV1 files are kept) | *Encoding › Re-encode*                               |
| 4  | Write title, description, TPDB id, exclusive tag and chapters      | always (only when changed)                           |
| 5  | Cover → upload                                                     | *Outputs › Cover image*, *Image hosts › Cover hosts* |
| 6  | Thumbnails sheet → upload                                          | *Outputs › Thumbnails sheet*, *Thumbnails* settings  |
| 7  | Animated previews and sheets (WebP/AVIF/WebM/GIF) → upload         | *Outputs › Video preview*, *Video preview* settings  |
| 8  | MediaInfo file                                                     | *Outputs › MediaInfo file*                           |
| 9  | BBCode template + tags (uses the uploaded image URLs)              | *Outputs › Upload template*                          |
| 10 | Move the files into the sub folder                                 | *Naming › Create a sub folder per file*              |
| 11 | Torrent, duplicate check, tracker form (you confirm)               | *Tracker upload*                                     |
| 12 | Add to the TPDB collection                                         | *Matching › Add to TPDB collection*                  |

If you cancel the tracker upload (or it fails), or cancel the job while a file is being processed, the app offers
to **undo the file**: it deletes the files and
uploaded images this run created (Pixhost automatically, Hamster/ImgBB/imgbox listed for manual deletion) and
moves the video back to the working folder under its original name.

A TPDB connection failure stops the job; you can resume it later. If a file has no match, that file is skipped
and you get a notification. A failed cover or image upload is logged and processing continues.

---

## Settings

Settings are edited in the **Settings** tab (admins). Every setting has an ⓘ tooltip, and each save keeps a backup
in `Configs/backups/`. New jobs copy the defaults; each job's own settings can be edited until the job runs.

| Tab (file)                                  | Sections and main settings                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
|---------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Processing defaults** (`processing.json`) | **Input**: working directory, ignored names, words stripped from names, moved-out folder · **Matching**: mode (free parse / strict / full manual), TPDB database (scenes / JAV / movies), OSHash first, re-match by embedded id, manual performers, TPDB collection, per-file UUIDs · **Review**: approve performers / date / title, duration-mismatch warning, chapter markers · **Naming**: file name mode (title / names / both / TPDB id / user input), title date format, stripped strings, ignore part / resolution / performer id, suffix for all files, sub folder per file, maximum name and path length · **Encoding**: re-encode, codec (HEVC / AV1), device (CPU / GPU), CRF / preset / GPU quality, CPU fallback, downscale > 1080p, CPU limit, chapters, keep original, single audio track · **Metadata**: exclusive tag · **Outputs**: cover (+ regeneration mode), thumbnails, video preview, MediaInfo, performer images, upload template, image format (WebP / AVIF / JPG / PNG / BMP), template file, icon base URL, font · **Portraits**: size, zoom, face score, blur, posters per performer, format · **Image hosts**: fallback / all, host lists for cover, thumbnails, previews, portraits · **Tracker upload**: trackers, release mode, cleanup, wait per upload · **Notifications**: Telegram |
| **Thumbnails** (`thumbnails.json`)          | Count, width, columns, padding, suffix, file info header, compact vertical layout, alternate layout, first frame, regeneration mode, font                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Video preview** (`video_preview.json`)    | WebP / AVIF / WebM / GIF previews and sheets, resolution (480p / 720p / 1080p), segments and duration, grid columns, GIF segments, fps per format, AVIF quality (CRF), timestamp overlay, transition, scene-change threshold, black bars, file info, cut point options, excluded files, custom output folder, temp files, font                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **Tracker sites** (`tracker.json`)          | Browser (Firefox / Chrome, driver and profile paths, timeouts, idle close), torrent (excluded suffixes, piece size), sites (login / upload URL, category, 2FA, form selectors)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **Application** (`app.json`)                | **Web server**: bind address, port, base path, log level, open browser, browser command · **Authentication**: session secret and lifetime, secure cookies, lockout · **Jobs**: concurrent jobs, CPU and GPU encode slots, require preview, retention, prompt log lines, finish notifications · **Resource sync** · **Environment**: Python range, ffmpeg / ffprobe paths and minimum versions · **Hardware acceleration**: GPU backend, GPU decode and encode for thumbnails and previews, CPU retry, VAAPI device · **Updates**: channel (stable / dev / off), check on start, repository, branches                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **Credentials** (`credentials.secret`)      | TPDB token and API URLs, image host accounts, tracker accounts and announce URLs, Telegram, GitHub token. Secrets are masked in the UI                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

The full list of keys, and how the 13.x key names map to them, is in the
[configuration page](wiki/03-configuration.md).

---

## Image formats and hosts

| Format    | Cover / thumbnails / portraits | Animated previews                                 | Hosts that accept it       |
|-----------|--------------------------------|---------------------------------------------------|----------------------------|
| WebP      | ✓ (default)                    | ✓ (default; used by the template and the tracker) | Hamster, Pixhost, ImgBB    |
| **AVIF**  | ✓                              | ✓ (AV1, much smaller files)                       | **Pixhost only**           |
| JPG / PNG | ✓                              | –                                                 | all (imgbox: JPG/PNG only) |
| GIF       | –                              | ✓                                                 | Hamster, Pixhost, ImgBB    |
| WebM      | –                              | ✓ (kept locally)                                  | –                          |

- AVIF previews are encoded with SVT-AV1, or libaom-av1 when SVT-AV1 is missing. AVIF images are saved with
  Pillow (11.3 or newer).
- Hosts that do not accept a file's format are skipped with a warning; in fallback mode the next host is tried.
  The preview warns about these cases in advance.
- The BBCode template and the tracker form keep using the **WebP** preview sheet and preview, because HF supports
  WebP and GIF only. AVIF previews are extra files, uploaded where they are accepted.

---

## Versions and updates

- The installed version comes from the `VERSION` file. It is shown in the sidebar, in the Tools tab and in the
  console banner.
- **Settings › Application › Updates › Update channel**:
    - **stable** compares with `VERSION` on the `master` branch;
    - **dev** compares with `VERSION` on the `dev` branch;
    - **off** turns the check off.
- The check runs when the server starts and every 6 hours. When a newer version exists, a notification appears
  in the browser, the sidebar shows a *vX available* badge, and *Tools › Version* shows how to update.
- **Manual check:** click the version number at the top of the sidebar (or *Check now* in Tools). A real check
  runs at most once every 5 minutes; within that time you see the last result and when you can check again.
- The check only reports; it never downloads or installs anything. To update, run `git pull origin <branch>`,
  then `pip install -r requirements.txt`, and restart.
- Found a bug? Use **Report an issue** (About page or sidebar). It opens
  [a new GitHub issue](https://github.com/edstagdh/File_Prepare_HF/issues/new) with your version, OS, Python and
  ffmpeg filled in; add what happened and the relevant log lines.
- Versions are `MAJOR.MINOR`. Every change raises the version, and the commit message names it.

---

## Command line

| Command                                                   | Purpose                                                       |
|-----------------------------------------------------------|---------------------------------------------------------------|
| `python main.py [--no-browser]`                           | Start the web interface                                       |
| `python run.py web [--host H --port P]`                   | Server only                                                   |
| `python run.py preview` / `python run.py process [--yes]` | Terminal dry run / processing with the saved default settings |
| `python run.py migrate-config [--overwrite]`              | Convert 13.x config files                                     |
| `python run.py create-user NAME --role admin`             | Create a web user                                             |
| `python run.py check-ffmpeg`                              | ffmpeg / ffprobe report                                       |
| `python run.py sync-resources`                            | Sync `Resources/` from GitHub                                 |
| `python run.py pixhost list\|delete <url>`                | Manage Pixhost uploads                                        |

---

## Files and folders

```
main.py               web app launcher (banner, URLs, browser)
run.py                command line
VERSION               installed version (compared by the update check)
Configs/              settings (*.json, credentials.secret; *.example.json are templates) + backups/
Resources/            templates, fonts, BBCode icon map, performer image map, face detection model
data/                 jobs (data/jobs/<id>.json + .log), performer image candidates, links (git-ignored)
Logs/                 application logs
fileprep/             application package (config, pipeline, jobs, media, tpdb, uploaders, tracker, web…)
docs/                 this README and the wiki pages
tests/                pytest suite
```

Files written next to each video use the new base name: `<name>.mp4`, `<name>.webp` (cover),
`<name>_thumbnails.webp`, `<name>_preview.webp` / `.avif`, `<name>_preview_sheet.webp` / `.avif`,
`<name>_mediainfo.txt`, `<name>_template.txt`, `<name>_tags.txt` and `<name>_<host>.txt` (uploaded URLs).

---

## Documentation

- [Getting started](wiki/01-getting-started.md)
- [Jobs, preview and the queue](wiki/02-jobs.md)
- [Configuration and renamed keys](wiki/03-configuration.md)
- [Image hosts and tracker upload](wiki/04-image-hosts-and-tracker.md)
- [Tools and modules](wiki/05-tools-and-modules.md)
- [Architecture (developers)](wiki/06-architecture.md)
- [Troubleshooting](wiki/07-troubleshooting.md)

### File name input (strict matching mode)

```
STUDIO_NAME.YY.MM.DD.PERFORMER_FNAME.PERFORMER_LNAME.EXTENSION
STUDIO_NAME.YY.MM.DD.PERFORMER_FNAME.PERFORMER_LNAME.PART.NUMBER.EXTENSION
STUDIO_NAME.YY.MM.DD.PERFORMER1_FNAME.PERFORMER1_LNAME.and.PERFORMER2_FNAME.PERFORMER2_LNAME.EXTENSION
```

The default mode, `free_string_parse`, sends the file name to the TPDB parser. `full_manual` asks you for all
metadata.

---

## Development

```bash
pip install pytest
python -m pytest -q tests
```

The code is organised in modules, one per processing step (see [Architecture](wiki/06-architecture.md)). A new
setting needs three things:
1. a field in `fileprep/config/models.py`;
2. a tooltip in `fileprep/config/help.py`;
3. a regenerated `Configs/*.example.json`.

The settings form appears in the UI automatically.

## License

MIT – see [LICENSE](../LICENSE).
