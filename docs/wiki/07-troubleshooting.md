# Troubleshooting

| Symptom                                                                       | What to do                                                                                                                                                                 |
|-------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Job stays **queued**                                                          | Another HEVC job is running, the directory is used by another job, or the concurrent job limit is reached (shown on the Jobs page).                                        |
| "Settings changed since the preview"                                          | Run the preview again - any settings change invalidates it.                                                                                                                |
| "The input files changed since the preview"                                   | Files were added/renamed/modified - run the preview again.                                                                                                                 |
| Preview error `TPDB_CONNECTION` / job failed with "TPDB API is not reachable" | Check the network and the TPDB status, or the API token (HTTP 401/403), then preview again / Resume.                                                                       |
| Warning `E38_NO_MATCH`                                                        | Search the scene on TPDB and use **Set TPDB UUID**, or **Move out** the file; otherwise the run skips it.                                                                  |
| Preview error `DUPLICATE_TARGET`                                              | Two files resolve to the same name: exclude one, change the file name mode, or fix the match.                                                                              |
| Preview error `TARGET_EXISTS`                                                 | A different file already has the target name in the working directory.                                                                                                     |
| `E33_FFMPEG` / missing encoder                                                | Update ffmpeg (Tools > ffmpeg) or set its path in Settings > Application > Environment.                                                                                    |
| "No cover image could be downloaded"                                          | The scene image and the TPDB copy failed after 3 attempts each (e.g. 502 from the studio site). The file continues without a cover and the template's cover is left empty. |
| Thumbnails fail on short clips                                                | The sheet needs enough duration for the requested number of thumbnails; lower the count.                                                                                   |
| Tracker browser does not start                                                | Install Firefox/Chrome; leave driver paths empty to let Selenium Manager download the driver.                                                                              |
| Locked out of the login                                                       | Wait for the lockout to expire or restart the server; reset a password with an admin account or `python run.py create-user` for a new admin.                               |
| Server restarted during a job                                                 | The job becomes **interrupted** - press Resume; finished files are skipped.                                                                                                |

Logs: each job's log is on its page and in `data/jobs/<id>.log`; the application log is in `Logs/`.

Failures are logged as **one line** (`<what failed>: <reason>`) - no tracebacks for cancellations, network, image
host, browser or file errors. Only an unexpected error (a bug) ends with "(details in the log file in Logs/)"; its
full traceback is written to the application log in `Logs/` only - attach it when you use **Report an issue**.
