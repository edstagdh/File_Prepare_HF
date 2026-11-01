import asyncio
import json

from fileprep import paths
from fileprep.config.models import JobConfig
from fileprep.media import portrait_picker as picker
from fileprep.uploaders import service


def test_performer_key_matches_template_lookup():
    assert picker.performer_key("Jane Doe (Janey)") == "Jane Doe"
    assert picker.performer_key("Ann_Lee!") == "AnnLee"


def test_save_url_and_choose_with_fallback(tmp_path, monkeypatch):
    images = tmp_path / "Performers_Images.json"
    images.write_text(json.dumps({"zed": "https://x/z.webp", "jane doe": "https://old"}))
    monkeypatch.setattr(paths, "PERFORMER_IMAGES_FILE", images)
    monkeypatch.setattr(paths, "RESOURCES_DIR", tmp_path)
    monkeypatch.setattr(picker, "PORTRAITS_DIR", tmp_path / "portraits")
    folder = picker._folder("job1", "Jane Doe")
    folder.mkdir(parents=True)
    (folder / "upload_1-face-1.webp").write_bytes(b"x")

    async def fake_upload(host, path, base, label):
        if host == "hamster":
            return False  # first host fails -> next one
        (folder / f"{base}_{host}.txt").write_text(json.dumps({f"{base} - {label}": [{"image_url": f"https://{host}/img.webp"}]}))
        return True

    monkeypatch.setattr(service, "_upload", fake_upload)
    config = JobConfig()
    config.processing.image_hosts.portrait_hosts = ["hamster", "pixhost"]
    url = asyncio.run(picker.choose("job1", "Jane Doe", "upload_1-face-1.webp", config))
    saved = json.loads(images.read_text())
    assert url == "https://pixhost/img.webp" and saved["Jane Doe"] == url and "jane doe" not in saved
    assert list(saved) == ["Jane Doe", "zed"]  # sorted, old case variant replaced
    assert (tmp_path / ".backups").is_dir()
    assert picker.list_candidates("job1", "Jane Doe") == [] and not folder.exists()  # other candidates deleted


def test_link_tpdb_validates_and_stores(tmp_path, monkeypatch):
    import pytest
    from fileprep.config.models import Credentials
    from fileprep.tpdb import api_client
    monkeypatch.setattr(picker, "TPDB_LINKS_FILE", tmp_path / "links.json")
    creds = Credentials()
    creds.tpdb.api_token = "t"
    monkeypatch.setattr("fileprep.config.store.get_credentials", lambda: creds)
    with pytest.raises(ValueError):
        asyncio.run(picker.link_tpdb("Jane Doe", "not-a-uuid"))

    async def fake_request(url, token, query, retries, delay, mode="parse"):
        return {"data": {"name": "Jane Doe", "id": query}} if query.startswith("1111") else None

    monkeypatch.setattr(api_client, "send_request", fake_request)
    uuid = "11111111-2222-3333-4444-555555555555"
    assert asyncio.run(picker.link_tpdb("Jane Doe", uuid))["tpdb_name"] == "Jane Doe"
    assert picker.load_links() == {"Jane Doe": uuid}
    with pytest.raises(ValueError):
        asyncio.run(picker.link_tpdb("Anna", "99999999-2222-3333-4444-555555555555"))


def test_prompt_list_web_and_console():
    from fileprep.runtime import Question, Runtime, prompt_list, use_runtime

    class Web:
        supports_list = True

        async def ask(self, q: Question):
            assert q.kind == "list" and q.item_label == "Performer"
            return '["Jane Doe", " ", "Anna Lee"]'

    class Console:
        def __init__(self):
            self.answers = iter(["Jane Doe", "Anna Lee", ""])

        async def ask(self, q):
            return next(self.answers)

    for prompter in (Web(), Console()):
        with use_runtime(Runtime(config=JobConfig(), prompter=prompter)):
            assert asyncio.run(prompt_list("Performers", item_label="Performer")) == ["Jane Doe", "Anna Lee"]
