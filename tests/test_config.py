import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fileprep.config import legacy
from fileprep.config.models import JobConfig, ProcessingSettings
from fileprep.config.store import ConfigStore

LEGACY = {
    "jav_only_api_mode": False, "movies_only_api_mode": True, "movies_scenes_mode": "full", "scene_matching_mode": "strict",
    "working_path": "G:\\ffmpeg", "filename_mode": "title", "re_encode_hevc": False, "re_encode_hevc_CRF": 22,
    "title_date_mode": "7", "hamster_upload_cover": True, "pixhost_upload_cover": True, "imgbb_upload_cover": True,
    "hamster_upload_previews": True, "imgbox_upload_previews": True, "upload_to_tracker": True, "tracker_mode": ["hf", "emp"],
    "create_template_file": True, "image_output_format": "PNG", "bad_words": [".X"], "use_notifier": True,
}


def test_processing_migration():
    p = legacy.migrate_processing(LEGACY, {"hamster_site_url": "https://img.example"})
    assert p.matching.api_mode == "movies" and p.matching.movie_entry_type == "full" and p.matching.matching_mode == "strict"
    assert p.input.working_directory == "G:\\ffmpeg" and p.input.filename_strip_words == [".X"]
    assert p.naming.title_date_format == "YYYY.MM.DD"
    assert p.encoding.re_encode is False and p.encoding.crf == 22 and p.encoding.device == "cpu"
    assert p.image_hosts.mode == "all"
    assert p.image_hosts.cover_hosts == ["imgbb", "hamster", "pixhost"]  # legacy template priority order
    assert p.image_hosts.preview_hosts == ["hamster"]  # imgbox cannot host previews
    assert p.tracker.enabled and p.tracker.trackers == ["hf", "emp"]
    assert p.outputs.image_format == "png" and p.outputs.template_icon_base_url == "https://img.example"
    assert p.notifications.enabled


def test_credentials_and_tracker_migration():
    creds = legacy.migrate_credentials({"api_auth": "T", "hamster_api_key": "K",
                                        "trackers": {"emp_tracker_u": "u", "emp_tracker_p": "p", "emp_tracker_ann_url": "a"},
                                        "tg_bot_token": "B"})
    assert creds.tpdb.api_token == "T" and creds.image_hosts.hamster_api_key == "K"
    assert creds.trackers["emp"].username == "u" and creds.trackers["emp"].announce_url == "a"
    assert creds.notifiers.telegram_bot_token == "B"
    tracker = legacy.migrate_tracker({"browser": "chrome", "2FA": True, "list_suffixes_ignore": ["_x.txt"],
                                      "trackers_urls": {"hf_tracker_upload_url": "https://hf/upload"},
                                      "form_fields": [{"by": "id", "value": "title"}]})
    assert tracker.browser.browser == "chrome" and tracker.sites["hf"].two_factor_auth
    assert tracker.sites["hf"].upload_url == "https://hf/upload" and tracker.torrent.excluded_suffixes == ["_x.txt"]
    assert tracker.sites["emp"].form.title_field.value == "title"  # selectors copied to every site


def test_v14_global_tracker_form_moves_into_each_site():
    from fileprep.config.models import TrackerConfig
    t = TrackerConfig.model_validate({"browser": {"two_factor_auth": True}, "form": {"title_field": {"by": "name", "value": "t"}},
                                      "sites": {"hf": {"upload_url": "u"}, "new": {"upload_url": "n", "form": {"title_field": {"value": "own"}}}}})
    assert t.sites["hf"].two_factor_auth and t.sites["hf"].form.title_field.value == "t"
    assert t.sites["new"].form.title_field.value == "own"  # a site's own selectors win


def test_real_legacy_example_migrates():
    example = Path(__file__).resolve().parent / "data" / "legacy_Config.json"
    p = legacy.migrate_processing(json.loads(example.read_text()))
    assert p.naming.filename_mode == "names" and p.outputs.cover_regeneration_mode == "force keep"


def test_store_bootstrap_migrates_and_round_trips(tmp_path):
    (tmp_path / "Configs").mkdir()
    (tmp_path / "Configs" / "Config.json").write_text(json.dumps(LEGACY))
    (tmp_path / "creds.secret").write_text(json.dumps({"api_auth": "abc"}))
    store = ConfigStore(tmp_path / "Configs", tmp_path)
    messages = store.bootstrap()
    assert any("Migrated" in m for m in messages)
    assert store.credentials().tpdb.api_token == "abc"
    proc = store.processing()
    proc.encoding.crf = 20
    store.save("processing", proc)
    assert store.processing().encoding.crf == 20
    assert (tmp_path / "Configs" / "backups").is_dir()


def test_validation_rejects_bad_values():
    with pytest.raises(ValidationError):
        ProcessingSettings.model_validate({"encoding": {"crf": 40}})
    with pytest.raises(ValidationError):
        JobConfig.model_validate({"processing": {"image_hosts": {"cover_hosts": ["unknown"]}}})
