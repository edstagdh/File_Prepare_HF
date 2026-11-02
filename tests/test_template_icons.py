"""Icon sets per template: HF_Template_<Theme>.txt uses the <Theme>_<key> entries of BBCode_Images.json."""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from fileprep import paths, runtime
from fileprep.template import bbcode, preview
from fileprep.template.icons import icons_for_template, template_theme

THEMES = ["Afterhours", "Datasheet", "Edition", "Reel"]
ICON_KEYS = ["bg", "15", "24", "25", "30", "50", "60", "2160p", "1440p", "1080p", "720p", "SD",
             "hevc", "avc", "av1", "mp4", "tpdb_button"]
BASE_ONLY = ["desc_button", "release_date_button", "performers_button", "mediainfo_button", "screens_button"]


def shipped_icons() -> dict:
    return json.loads(paths.BBCODE_IMAGES_FILE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,theme", [
    ("HF_Template.txt", ""), ("EMP_Template.txt", ""), ("HF_Template_Reel.txt", "Reel"),
    (r"G:\DEV\Resources\HF_Template_Afterhours.txt", "Afterhours"), ("/app/Resources/HF_Template_Edition.txt", "Edition"),
])
def test_theme_from_file_name(name, theme):
    assert template_theme(name) == theme


def test_base_template_keeps_base_icons():
    icons = {"bg": "/base.png", "Reel_bg": "/reel.png"}
    assert icons_for_template(icons, "HF_Template.txt")["bg"] == "/base.png"


def test_theme_overrides_and_falls_back_case_insensitively():
    icons = {"bg": "/base.png", "desc_button": "/desc.png", "reel_bg": "/reel.png"}
    themed = icons_for_template(icons, "HF_Template_Reel.txt")
    assert themed["bg"] == "/reel.png" and themed["desc_button"] == "/desc.png"


def test_unknown_theme_uses_base_icons():
    assert icons_for_template({"bg": "/base.png"}, "HF_Template_Nope.txt")["bg"] == "/base.png"


@pytest.mark.parametrize("theme", THEMES)
def test_shipped_map_is_complete(theme):
    icons = shipped_icons()
    for key in ICON_KEYS:
        assert icons[f"{theme}_{key}"] == f"/images/2026/11/02/{theme}_{key}.png"
    for key in BASE_ONLY:
        assert key in icons


def generate(tmp_path, monkeypatch, template, fps=24, resolution="1080p", codec="hevc", tpdb="https://example.org/s"):
    monkeypatch.setattr(runtime, "config", lambda: SimpleNamespace(
        processing=SimpleNamespace(outputs=SimpleNamespace(template_icon_base_url="https://hamsterimg.net"))))
    performers = tmp_path / "performers.json"
    performers.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths, "PERFORMER_IMAGES_FILE", performers)
    (tmp_path / "x_mediainfo.txt").write_text("General\nFormat : MPEG-4\n", encoding="utf-8")
    (tmp_path / "x.webp").write_bytes(b"0")
    assert asyncio.run(bbcode.generate_template_video(
        "T", "Scene", "Studio", "2026-November-01", "Desc", [["Jane Doe"]], fps, resolution, False, codec, ".mp4",
        str(tmp_path), str(tmp_path / "x.mp4"), "x", str(paths.resource(template)), "14.14", [], [], "webp",
        False, "", tpdb))
    return (tmp_path / "x_template.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("fps,resolution,codec", [
    (15, "SD", "avc"), (24, "720p", "hevc"), (25, "1080p", "av1"), (30, "1440p", "hevc"), (50, "2160p", "avc"), (60, "1080p", "hevc"),
])
def test_generated_template_uses_only_its_icons(tmp_path, monkeypatch, theme, fps, resolution, codec):
    out = generate(tmp_path, monkeypatch, f"HF_Template_{theme}.txt", fps, resolution, codec)
    assert not re.findall(r"\{[A-Z_]+\}", out)
    for key in (str(fps), resolution, codec, "mp4", "bg", "tpdb_button"):
        assert f"https://hamsterimg.net/images/2026/11/02/{theme}_{key}.png" in out
    used = set(re.findall(r"https://hamsterimg\.net/images/[^\[\],\s]+", out))
    assert used and all(f"/{theme}_" in url for url in used)


def test_base_template_output_is_unchanged_by_themes(tmp_path, monkeypatch):
    out = generate(tmp_path, monkeypatch, "HF_Template.txt")
    assert not re.search(r"/(Afterhours|Datasheet|Edition|Reel)_", out)
    assert "/images/2025/10/03/1080p_small77cdb8aab8ac9b6f.png" in out


@pytest.mark.parametrize("theme", THEMES)
def test_preview_uses_the_template_icons(theme):
    bbcode_text = preview.render_preview(f"HF_Template_{theme}.txt")["bbcode"]
    assert f"/images/2026/11/02/{theme}_1080p.png" in bbcode_text
