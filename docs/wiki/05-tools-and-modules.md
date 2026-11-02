# Tools and modules

## Version and updates (Tools)

The installed version (file `VERSION`) is shown in the sidebar and at the top of **Tools**. The app compares it with
the `VERSION` file of the GitHub branch of your **update channel** (**Settings > Application > Updates**):

| Channel | Compared with                                           |
|---------|---------------------------------------------------------|
| stable  | `master` branch (stable releases)                       |
| dev     | `dev` branch (development builds - newest, less tested) |
| off     | no check                                                |

The check runs when the server starts and every 6 hours (*Check on start*). When a newer version exists you get a
notification once per browser session, the sidebar shows *vX available*, and the Tools panel shows how to update
(`git pull origin <branch>`, `pip install -r requirements.txt`, restart). **Click the version number** at the top of
the sidebar (from any page) or **Check now** to check right away; to spare GitHub, a real check happens at most once
every 5 minutes - within that time you get the last result and when the next check is possible. *Newer than
channel* means your installation is ahead of that branch (e.g. a dev build on the stable channel). Nothing is ever
downloaded or installed automatically. A GitHub token in **Credentials > GitHub** is used when set (private forks).

## About

**About** (sidebar) shows the version and update status, links to the GitHub repository, the stable and
development branches, the documentation and the license, and the environment (version, update channel, OS,
Python, ffmpeg, folders). **Report an issue** - here and at the bottom of the sidebar - opens a new issue on GitHub
pre-filled with a short template and the environment (folder paths are not included); **Open issues** lists the
existing ones. The repository is the one set in **Settings > Application > Updates**.

## ffmpeg (Tools)

Shows the detected ffmpeg / ffprobe path, version, build date and the encoders the pipeline needs
(`libx265` for HEVC, `libwebp` for WebP previews, `libvpx-vp9` for WebM, `libsvtav1` / `libaom-av1` for AV1 encoding and AVIF previews). A tool passes when either its version
or its build date meets the minimum in **Settings > Application > Environment**. The page lists the update
sources for the server's operating system (Windows: gyan.dev / BtbN / winget, macOS: Homebrew / evermeet.cx,
Linux: static builds / distribution packages). After updating press **Re-check**. A custom ffmpeg location can
be set in the Environment settings.

## GPU acceleration (Tools)

Shows every GPU backend for the server's OS and what actually works on this machine: hardware decoding and
the H.264 / HEVC / AV1 encoders. Each one is tested by decoding a short clip and encoding a test pattern, so a
backend that ffmpeg lists but the hardware/driver cannot run is shown as unavailable. **Re-detect** after a
driver or ffmpeg update.

- **Thumbnails and preview segments** decode the source on the GPU and encode the intermediate H.264 clips with
  the GPU encoder; a failing GPU command is retried on the CPU. The final WebP / AVIF / GIF / WebM encoders
  always use the CPU.
- **Full video encoding** uses the GPU only when the job's Encoding tab selects *GPU*; the preview reports a
  missing GPU encoder (error, or warning with CPU fallback enabled).
- Configure it in **Settings > Application > Hardware acceleration**.

## Resource sync (Tools)

Keeps files in `Resources/` up to date from GitHub (performer image links, BBCode icon map, templates...).

- Add an entry: name, GitHub URL (a `github.com/.../blob/...` link is converted to its raw URL; raw and gist
  links work too), target file inside `Resources/`, and the mode:
    - **replace** - the remote file replaces the local one;
    - **merge_json** - remote keys are merged into the local JSON map (remote wins, local-only keys stay).
- **Check for updates** compares without writing; **Sync now** downloads, validates JSON, keeps the previous
  version in `Resources/.backups/` and writes atomically.
- Private repositories: put a read-only token in **Credentials > GitHub**.
- **Sync on start** (Application settings) syncs automatically when the server starts.
- Terminal: `python run.py sync-resources`.

## Resources

The **Resources** page lists the files in `Resources/` (templates, fonts, image maps, models) with size and date.
Click a file to preview it:

- **templates** - filled with sample data and rendered like the tracker shows BBCode (icons from
  `BBCode_Images.json`, cover / preview / thumbnail placeholders), plus the filled BBCode text.
  A template named `<Site>_Template_<Theme>.txt` uses the `<Theme>_<key>` entries of `BBCode_Images.json`
  (`HF_Template_Reel.txt` -> `Reel_1080p`, `Reel_bg`...); keys it does not define keep the base image;
- **fonts** - sample text in that font (editable) at several sizes;
- **JSON / text** - the content; **images** - the image.

## Pixhost delete links (Tools)

Lists Pixhost uploads that were not deleted yet and deletes them (terminal: `python run.py pixhost list|delete <url>`).

## Modules

Every processing step is an independent module (matching, encoding, thumbnails, previews, portraits, template,
each image host, tracker, notifier, resource sync). The Modules tab shows each one's state:

| State          | Meaning                                                                                       |
|----------------|-----------------------------------------------------------------------------------------------|
| ready          | Configured and enabled in the default job settings.                                           |
| off by default | Works, but is not enabled in the default job settings - enable it per job when needed.        |
| setup needed   | Something it needs is missing (credentials, a Resources file...) - the status line says what. |

Quick tests are available for TPDB, Telegram, Hamster, Pixhost and ffmpeg.
