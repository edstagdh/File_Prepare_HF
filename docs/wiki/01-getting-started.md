# Getting started

File Prepare HF prepares media files for upload: it matches each video with TPDB, renames it, re-encodes
it to HEVC, writes metadata, builds covers / thumbnail sheets / animated previews / an upload template,
uploads images, and can fill the tracker upload form for you. Everything is driven from this web interface.

## Install

1. Python 3.10 - 3.14 and a recent **ffmpeg + ffprobe** on PATH (check them in **Tools > ffmpeg**).
2. From the project folder:

```
python -m venv venv
venv\Scripts\activate          (Windows)   |   source venv/bin/activate   (Linux/macOS)
pip install -r requirements.txt
```

## Start the web interface

```
python main.py
```

Opening the browser is optional: turn off **Settings > Application > Web server > Open the browser** (or use
`--no-browser`). It uses the system default browser; with several Firefox profiles running, Firefox opens the
tab in the profile that was used last. To pick a browser or profile, set **Browser command**, e.g.
`"C:\Program Files\Mozilla Firefox\firefox.exe" -P Work -new-tab {url}`.

`main.py` prints the addresses (and your network addresses when listening on all interfaces), the ffmpeg
status and where settings and logs are stored. The browser opens `http://127.0.0.1:8765` (use `--no-browser` to skip that, or
`python run.py web` for the server only). Stop it with Ctrl+C. On the first visit you create the **administrator** account
(or run `python run.py create-user NAME --role admin` beforehand).

- Bind address, port and reverse-proxy base path: **Settings > Application > Web server** (restart needed).
- To reach it from another machine set the bind address to `0.0.0.0`, put HTTPS in front of it
  (reverse proxy) and enable **Secure cookies**.

## Coming from version 13.x

On the first start the old files are migrated automatically (they are never modified):

| Old file                            | New file                     |
|-------------------------------------|------------------------------|
| `Configs/Config.json`               | `Configs/processing.json`    |
| `Configs/Config_Thumbnails.json`    | `Configs/thumbnails.json`    |
| `Configs/Config_Video_Preview.json` | `Configs/video_preview.json` |
| `Configs/Config_Tracker.json`       | `Configs/tracker.json`       |
| `creds.secret`                      | `Configs/credentials.secret` |

Run `python run.py migrate-config --overwrite` to migrate again later. All keys were renamed - see
[Configuration](03-configuration.md) for the full mapping.

## Your first job

1. **Settings > Credentials**: enter your TPDB API token (and image host / tracker accounts if used).
2. **New job**: pick the working directory (Browse...), check the tabs, press **Save & preview**.
3. Answer any questions shown on the job page (scene selection, approvals...).
4. When the preview has no blocking issues press **Queue / start**.

## Updating

The sidebar shows the installed version; with the update check on (**Settings > Application > Updates**, stable =
`master`, dev = `dev`) a notification tells you when your channel has a newer version. Update with
`git pull origin <branch>`, `pip install -r requirements.txt`, then restart `main.py`.

The terminal mode still exists: `python run.py preview` (dry run) and `python run.py process`.
