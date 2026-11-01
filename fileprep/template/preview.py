"""
Template preview: fill an upload template with sample data and render its BBCode as HTML.

The HTML is an approximation of how Gazelle-style trackers display BBCode (BG, font, color, size, img,
url, table/tr/td, spoiler, mediainfo, center/align, b/i/u, BR). Icons come from Resources/BBCode_Images.json
and the configured icon base URL; cover / preview / thumbnail images are labelled placeholders.
"""

import html
import json
import re
from urllib.parse import quote

from fileprep import paths
from fileprep.config.store import store

SIZES = {"1": 10, "2": 13, "3": 16, "4": 18, "5": 24, "6": 32, "7": 48}


def _placeholder(label: str, width: int, height: int) -> str:
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}'>"
           f"<rect width='100%' height='100%' fill='#3b4252'/><text x='50%' y='50%' fill='#e5e9f0' font-family='sans-serif' "
           f"font-size='{max(14, height // 10)}' text-anchor='middle' dominant-baseline='middle'>{label}</text></svg>")
    return "data:image/svg+xml," + quote(svg)


def sample_values() -> dict[str, str]:
    """Values for every template placeholder (same keys as fileprep.template.bbcode)."""
    base = store.processing().outputs.template_icon_base_url.rstrip("/")
    try:
        icons = json.loads(paths.BBCODE_IMAGES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        icons = {}

    def icon(key: str, label: str) -> str:
        return f"{base}{icons[key]}" if key in icons else _placeholder(label, 200, 60)

    from fileprep import __version__
    return {
        "{BG}": icon("bg", "background"), "{DESC_BUTTON}": icon("desc_button", "Description"),
        "{RELEASE_DATE_BUTTON}": icon("release_date_button", "Release date"), "{PERFORMERS_BUTTON}": icon("performers_button", "Performers"),
        "{MEDIAINFO_BUTTON}": icon("mediainfo_button", "MediaInfo"), "{SCREENS_BUTTON}": icon("screens_button", "Screens"),
        "{FPS}": icon("30", "30 fps"), "{RESOLUTION}": icon("1080p", "1080p"), "{CODEC}": icon("hevc", "HEVC"),
        "{EXTENSION}": icon("mp4", "MP4"),
        "{TPDB_LINK}": f"[url=https://theporndb.net/scenes/sample-scene][img=100]{icon('tpdb_button', 'TPDB')}[/img][/url]",
        "{SCENE_STUDIO}": "Sample Studio(Parent Network)", "{NEW_TITLE}": "Sample Scene Title",
        "{SCENE_PRETTY_DATE}": "2025-January-01",
        "{SCENE_DESCRIPTION}": "A sample scene description that shows how the description block wraps inside its table cell.",
        "{FORMATTED_NAMES}": "Jane Doe, Anna Lee", "{IS_VERTICAL}": "no",
        "{MEDIA_INFO}": "General\nComplete name : Sample.Studio.25.01.01.Jane.Doe.mp4\nFormat : MPEG-4\nDuration : 32 min 10 s\n\n"
                        "Video\nFormat : HEVC\nWidth : 1 920 pixels\nHeight : 1 080 pixels\nFrame rate : 30.000 FPS",
        "{COVER_IMAGE}": _placeholder("COVER IMAGE", 700, 394), "{PREVIEW_SHEET}": _placeholder("PREVIEW SHEET (animated WebP)", 900, 506),
        "{STATIC_THUMBNAILS_SHEET}": _placeholder("THUMBNAILS SHEET", 900, 600), "{CODE_VERSION}": __version__,
    }


def fill_template(text: str) -> str:
    for placeholder, value in sample_values().items():
        text = text.replace(placeholder, value)
    return text


_SAFE = re.compile(r"[^#\w ,.%:/\-?=&;+()~@!']")


def _attr(value: str) -> str:
    return _SAFE.sub("", value).strip()


def _url(value: str) -> str:
    value = value.strip()
    return value if value.startswith(("http://", "https://", "data:image/")) else "#"


def bbcode_to_html(text: str) -> str:
    out = html.escape(text, quote=True)
    out = re.sub(r"\[mediainfo\](.*?)\[/mediainfo\]", lambda m: f"<pre class='mi'>{m.group(1).strip()}</pre>", out, flags=re.S | re.I)
    out = out.replace("\r\n", "\n")
    # images and links first (their URLs must not be touched by the other rules)
    out = re.sub(r"\[img=(\d+)\](.*?)\[/img\]", lambda m: f"<img src='{_url(html.unescape(m.group(2)))}' width='{m.group(1)}' alt=''>", out, flags=re.I | re.S)
    out = re.sub(r"\[img\](.*?)\[/img\]", lambda m: f"<img src='{_url(html.unescape(m.group(1)))}' alt=''>", out, flags=re.I | re.S)
    out = re.sub(r"\[url=([^\]]+)\]", lambda m: f"<a href='{_url(html.unescape(m.group(1)))}' target='_blank' rel='noopener'>", out, flags=re.I)
    out = re.sub(r"\[/url\]", "</a>", out, flags=re.I)

    def bg(m):
        parts = [p.strip() for p in html.unescape(m.group(1)).split(",")]
        style, colour = [], parts[0]
        if colour.startswith(("http://", "https://")):
            style.append(f"background-image:url({_attr(colour)});background-size:cover")
        else:
            style.append(f"background-color:{_attr(colour)}")
        width = next((p for p in parts[1:] if p.endswith("%")), None)
        if width:
            style.append(f"width:{_attr(width)}")
        if "center" in parts[1:]:
            style.append("margin:0 auto")
        return f"<div style='{';'.join(style)};padding:6px 0'>"

    rules = [
        (r"\[BG=([^\]]+)\]", bg), (r"\[/BG\]", "</div>"),
        (r"\[font=([^\]]+)\]", lambda m: f"<span style='font-family:{_attr(html.unescape(m.group(1)))}'>"), (r"\[/font\]", "</span>"),
        (r"\[color=([^\]]+)\]", lambda m: f"<span style='color:{_attr(html.unescape(m.group(1)))}'>"), (r"\[/color\]", "</span>"),
        (r"\[size=(\d)\]", lambda m: f"<span style='font-size:{SIZES.get(m.group(1), 16)}px'>"), (r"\[/size\]", "</span>"),
        (r"\[center\]", "<div style='text-align:center'>"), (r"\[/center\]", "</div>"),
        (r"\[align=(left|right|center|justify)\]", lambda m: f"<div style='text-align:{m.group(1).lower()}'>"), (r"\[/align\]", "</div>"),
        (r"\[spoiler=([^\]]*)\]", lambda m: f"<details><summary>{m.group(1)}</summary>"), (r"\[spoiler\]", "<details><summary>Spoiler</summary>"),
        (r"\[/spoiler\]", "</details>"),
        (r"\[table=([^\]]*)\]", lambda m: "<table style='border-collapse:collapse;margin:0 auto;width:"
                                          + (next((p for p in m.group(1).split(",") if p.strip().endswith("%")), "100%").strip()[:4]) + "'>"),
        (r"\[table\]", "<table style='margin:0 auto'>"), (r"\[/table\]", "</table>"),
        (r"\[tr\]", "<tr>"), (r"\[/tr\]", "</tr>"),
        (r"\[td=([\d.]+%)\]", lambda m: f"<td style='width:{m.group(1)};vertical-align:top;padding:4px'>"),
        (r"\[td\]", "<td style='vertical-align:top;padding:4px'>"), (r"\[/td\]", "</td>"),
        (r"\[(b|i|u)\]", lambda m: f"<{m.group(1).lower()}>"), (r"\[/(b|i|u)\]", lambda m: f"</{m.group(1).lower()}>"),
        (r"\[BR\]", "<br>"),
    ]
    for pattern, repl in rules:
        out = re.sub(pattern, repl, out, flags=re.I)
    # newlines become line breaks, except right around table structure (browsers would hoist them)
    out = re.sub(r"\n(?=\s*</?(table|tr|td)\b)|(?<=<tr>)\n|(?<=</td>)\n|(?<=</tr>)\n", "", out)
    return out.replace("\n", "<br>")


def render_preview(template_name: str) -> dict:
    path = paths.resource(template_name)
    if path.resolve().parent != paths.RESOURCES_DIR.resolve() or not path.is_file():
        raise FileNotFoundError(template_name)
    filled = fill_template(path.read_text(encoding="utf-8"))
    body = bbcode_to_html(filled)
    page = ("<!doctype html><html><head><meta charset='utf-8'><style>body{margin:0;background:#1e1e1e;color:#ddd;"
            "font:14px/1.4 sans-serif}img{max-width:100%}pre.mi{text-align:left;white-space:pre-wrap;background:#111;padding:8px}"
            "summary{cursor:pointer;color:#8ab4f8}a{color:#8ab4f8}</style></head><body>" + body + "</body></html>")
    return {"name": template_name, "bbcode": filled, "html": page,
            "placeholders": sorted(set(re.findall(r"\{[A-Z_]+\}", path.read_text(encoding="utf-8"))))}
