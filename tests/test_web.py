from fileprep.config.models import Credentials
from fileprep.web import forms, markdown
from fileprep.web.security import LoginThrottle, hash_password, verify_password


def test_password_hashing():
    stored = hash_password("correct-horse-1")
    assert verify_password("correct-horse-1", stored) and not verify_password("wrong", stored)


def test_throttle_locks_after_failures():
    t = LoginThrottle()
    key = ("u", "1.2.3.4")
    for _ in range(3):
        t.failed(key)
    assert t.locked_for(key, 3, 1) > 0
    t.succeeded(key)
    assert t.locked_for(key, 3, 1) == 0


def test_secrets_masked_and_merged():
    creds = Credentials()
    creds.tpdb.api_token = "secret"
    creds.trackers["emp"].password = "pw"
    data, flags = forms.mask_secrets(creds)
    assert data["tpdb"]["api_token"] == "" and flags["tpdb.api_token"]
    assert data["trackers"]["emp"]["password"] == "" and flags["trackers.emp.password"]
    merged = forms.merge_secrets(data, creds)
    assert merged["tpdb"]["api_token"] == "secret" and merged["trackers"]["emp"]["password"] == "pw"


def test_job_form_visibility_rules():
    sections = {s["key"]: s for s in forms.job_form()}
    assert sections["thumbnails"]["visible_if"] == "processing.outputs.thumbnails_sheet"
    assert sections["processing.portraits"]["visible_if"] == "processing.outputs.performer_portraits"
    crf = next(f for f in sections["processing.encoding"]["fields"] if f["name"] == "crf")
    assert crf["visible_if"] == "processing.encoding.codec == hevc" and crf["type"] == "int"


def test_markdown_renders_tables_and_escapes():
    html = markdown.render("# T\n\n| a | b |\n|---|---|\n| 1 | <x> |\n\n- item `code`\n")
    assert "<table>" in html and "&lt;x&gt;" in html and "<code>code</code>" in html


def test_template_preview_renders_and_escapes(monkeypatch, tmp_path):
    from fileprep import paths
    from fileprep.template import preview
    template = tmp_path / "T.txt"
    template.write_text("[center][size=5]Title: {NEW_TITLE}[/size][/center]\n[img=100]{CODEC}[/img] <script>x</script>\n"
                        "[url=javascript:alert(1)]bad[/url][spoiler=More][b]x[/b][/spoiler]")
    monkeypatch.setattr(paths, "RESOURCES_DIR", tmp_path)
    monkeypatch.setattr(paths, "resource", lambda name: tmp_path / name)
    r = preview.render_preview("T.txt")
    body = r["html"].split("<body>")[1]
    assert "Sample Scene Title" in body and "font-size:24px" in body and "<details><summary>More</summary>" in body
    assert "<script>" not in body and "javascript:" not in body and "[" not in body.replace("&#x27;", "")
    assert "{NEW_TITLE}" in r["placeholders"]
