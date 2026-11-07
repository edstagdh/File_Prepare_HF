import asyncio

import pytest

from fileprep.config.models import NamingSettings
from fileprep.pipeline import naming


def run(coro):
    return asyncio.run(coro)


def test_flags_and_suffix():
    flags = naming.detect_flags("Studio.24.01.15.Jane.Doe.BTS.mp4")
    assert flags.flags["bts"] and flags.suffix == "BTS"
    assert flags.clean_name == "studio.24.01.15.jane.doe.mp4"
    assert naming.detect_flags("Studio.24.01.15.Jane.Doe.mp4").suffix == ""


def test_pre_suffix_rules():
    clean = "studio.24.01.15.jane.doe.part.2.1080p.mp4"
    assert naming.detect_pre_suffix(clean, NamingSettings(ignore_part_number=False, ignore_resolution_tag=True)) == ".Part.2"
    # legacy quirk: the leading dot of the part token is dropped when a resolution follows
    assert naming.detect_pre_suffix(clean, NamingSettings(ignore_part_number=False, ignore_resolution_tag=False)) == "Part.2.1080p"
    assert naming.detect_pre_suffix(clean, NamingSettings(ignore_part_number=True, ignore_resolution_tag=True)) == ""


def test_title_date_formats():
    assert naming.format_scene_date("2025-01-01", "YYYY-Month-DD").pretty == "2025-January-01"
    assert naming.format_scene_date("2025-01-01", "YY.MM.DD").pretty == "25.01.01"
    assert naming.format_scene_date("2025-01-01", "YY-Month-DD").pretty == "25-January-01"
    assert naming.format_scene_date("0000-00-00", "YYYY-MM-DD").pretty == ""


def test_name_candidates_and_fallback():
    date = naming.format_scene_date("2024-01-15", "YYYY-Month-DD")
    c = run(naming.build_name_candidates("Studio Name", date, [["Jane Doe", 1], ["Anna Lee", 2]], "Morning Coffee", 123, "", "", []))
    assert c.names == "StudioName.24.01.15.Anna.Lee.and.Jane.Doe"
    assert c.both == "StudioName.24.01.15.Anna.Lee.and.Jane.Doe.Morning.Coffee"
    assert c.tpdb_id == "StudioName.24.01.15.123"
    name, warnings = naming.select_filename(c, "both", 200)
    assert name == c.both and not warnings
    name, warnings = naming.select_filename(c, "both", 45)
    assert name == c.names and warnings
    name, _ = naming.select_filename(c, "title", 10)
    assert name == c.tpdb_id


def test_title_builder():
    date = naming.format_scene_date("2024-01-15", "YYYY-Month-DD")
    title, studio_info, tags, has_performers = run(naming.build_title("Site", "Studio", date, "Scene", [["Jane Doe", 1]], "BTS"))
    assert title == "Site(Studio) - 2024-January-15 - Scene - Jane Doe - BTS"
    assert tags == ["Site", "Studio"] and has_performers
    title, *_, has_performers = run(naming.build_title("Site", "Site", date, "Scene", [], ""))
    assert title == "Site - 2024-January-15 - Scene" and not has_performers


def test_studio_display_modes():
    assert naming.studio_display("Site", "Network", "site_studio") == ("Site(Network)", ["Site", "Network"])
    assert naming.studio_display("Site", "Network", "site") == ("Site", ["Site"])
    assert naming.studio_display("Site", "Network", "studio") == ("Network", ["Network"])
    for studio in (None, "", "Site"):  # no different parent studio: always the site
        assert all(naming.studio_display("Site", studio, m) == ("Site", ["Site"]) for m in naming.STUDIO_MODES)
    with pytest.raises(ValueError):
        naming.studio_display("Site", "Network", "bogus")


def test_approve_studio(monkeypatch):
    import asyncio

    from fileprep.tpdb import scene_review

    def run(*answers):
        queue = iter(answers)

        async def fake_prompt(_=""):
            return next(queue)

        async def no_pause(*_):
            return None

        monkeypatch.setattr(scene_review, "prompt", fake_prompt)
        monkeypatch.setattr(scene_review, "notify_and_pause", no_pause)
        return asyncio.run(scene_review.approve_studio("Site", "Network", "site_studio", None))

    assert run("") == ("Network", None)  # Enter approves, the job's display applies
    assert run("2", "a") == ("Network", "site")  # chosen display
    assert run("2", "s") == ("Network", None)  # skipped
    assert run("Other", "r", "-", "a") == (None, None)  # restart, then no studio
    assert run("Other", "a") == ("Other", None)
