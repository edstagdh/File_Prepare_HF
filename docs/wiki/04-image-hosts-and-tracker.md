# Image hosts and tracker upload

## Image hosts with fallback

Each asset type has an **ordered** host list (**Image hosts** tab): cover, thumbnails sheet and previews.

- **fallback** (recommended): hosts are tried in order; the first success wins. A host that fails, is not
  configured or does not accept the file type is skipped and the next one is tried.
- **all**: every listed host receives the file (the 13.x behaviour; migrated configs start in this mode).

| Host    | Formats                                      | Credentials                                                            |
|---------|----------------------------------------------|------------------------------------------------------------------------|
| hamster | webp, jpg, png, gif, bmp (animated previews) | API key, album id, site URL                                            |
| pixhost | jpg, png, gif, webp, avif (10 MB max)        | none - delete links are kept in `Resources/pixhost_manage_links.jsonl` |
| imgbb   | webp, jpg, png, gif, bmp                     | username / password (uses a Chrome browser)                            |
| imgbox  | jpg, png only                                | none                                                                   |

**AVIF** (cover, thumbnails, portraits via *Image format*, and the *AVIF preview / sheet* options) is accepted by
**Pixhost only**: other hosts are skipped for AVIF files with a warning (the preview lists them beforehand). The
template and the tracker always use the WebP preview and sheet (HF accepts WebP / GIF).

The upload template and the tracker form use the URL of the first host (in list order) that has the image.
A file above a host's size limit (Pixhost: 10 MB) is skipped for that host before uploading. Upload failures are
logged but do not fail the file; the preview reports host lists without any usable host.

## Tracker upload

Requirements (checked by the preview): **Create a sub folder per file**, the **Upload template**, a WebP
preview uploaded to at least one preview host, the site in **Settings > Tracker sites** and its account +
announce URL in **Settings > Credentials**.

### Tracker sites

**Settings > Tracker sites** holds one entry per tracker; add or remove sites there. Each site has its own:

- login and upload page URLs, the category text, and whether the login needs 2FA;
- **form selectors** for every field of its login and upload pages (by id, name, CSS, XPath, class, tag or
  link text) - change them when a site changes its HTML;
- the dupe check: button, the two places the result may appear, and the texts meaning "possible duplicates"
  and "no duplicates". Leave the button value empty when a site has no dupe check.

A new site appears automatically in the job's **Trackers** list and gets an account entry in **Credentials**.
Older tracker.json files (one shared set of selectors) are converted on load: every site gets a copy.

How it works:

1. The first upload starts a dedicated browser thread for that tracker and logs in once (with 2FA you are
   asked to complete it in the browser window). The session stays logged in between files and jobs and closes
   after **Close idle browser after** minutes. See **Tools > Tracker browser sessions**.
2. For every file the job prepares the torrent, tags, description and preview URL, then the browser opens the
   upload page, fills the form, attaches the torrent and runs the dupe check.
3. You review and submit the form in the browser window, then answer **yes** on the job page.

### Cancelled or failed upload: undo the file

Answering **cancel** (or a failed upload to the first tracker of the file) asks **Undo this file?**:

- **undo** - deletes what the run created for that file: cover, thumbnails, previews, MediaInfo, template, tags,
  image host result files and the torrent; deletes the images this run uploaded to **Pixhost** (with their delete
  links); lists the **Hamster / ImgBB / imgbox** image URLs for you to delete on those websites (also sent as a
  notification); moves the video out of its sub folder back into the working folder under its **original name**
  and removes the sub folder when it is empty. Files that existed before the run and files the app does not know
  are never deleted. The video's new metadata and a re-encode stay. The file is marked failed, so **Resume**
  (after a new preview) processes it again from the start.
- **keep** - leaves everything as it is.

The tracker browser stays open and logged in after a cancel.

With **Wait for each upload before the next file** off (default) the next file is processed while you handle
the form; the job finishes once all uploads are confirmed.

Every tracker gets its own torrent (with that tracker's announce URL), created just before its upload. The
torrent is deleted once the dupe check has run (it is already attached to the form) and, at the latest, after
you confirm the submission. Helper files listed in **Torrent > Excluded
suffixes** are deleted after the last tracker when **Remove helper files after upload** is on.

The browser runs on the machine that runs this server.
