from pathlib import Path

from beets_mvp import create_app


def make_app(tmp_path: Path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "STATE_PATH": str(tmp_path / "config"),
        "BROWSE_ROOTS": [str(tmp_path)],
    })
    response = app.test_client().post("/setup", data={
        "inbox_path": str(tmp_path / "inbox"),
        "library_path": str(tmp_path / "library"),
    })
    assert response.status_code == 302
    return app


def test_health_and_empty_lists(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    assert client.get("/healthz").json == {"status": "ok"}
    assert client.get("/api/inbox").json == []
    assert client.get("/api/items").json == []
    assert app.config["APP_DB"] == str(tmp_path / "config" / "app.db")
    assert app.config["BEETS_DB"] == str(tmp_path / "config" / "library.db")
    assert app.config["BEETS_CONFIG"] == str(tmp_path / "config" / "config.yaml")


def test_default_state_path_uses_config_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STATE_PATH", raising=False)
    monkeypatch.delenv("BEETS_CONFIG", raising=False)

    app = create_app({"TESTING": True})

    assert app.config["STATE_PATH"] == str(tmp_path / "data" / "config")
    assert app.config["APP_DB"] == str(tmp_path / "data" / "config" / "app.db")
    assert app.config["BEETS_DB"] == str(tmp_path / "data" / "config" / "library.db")
    assert app.config["BEETS_CONFIG"] == str(tmp_path / "data" / "config" / "config.yaml")


def test_secret_key_is_generated_and_persisted(tmp_path, monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    state_path = tmp_path / "config"
    app = create_app({"TESTING": True, "STATE_PATH": str(state_path)})
    key = app.config["SECRET_KEY"]

    assert len(key) >= 64
    assert (state_path / "secret.key").read_text().strip() == key

    restarted = create_app({"TESTING": True, "STATE_PATH": str(state_path)})
    assert restarted.config["SECRET_KEY"] == key


def test_explicit_secret_key_overrides_generated_key(tmp_path):
    app = create_app({"TESTING": True, "STATE_PATH": str(tmp_path / "config"), "SECRET_KEY": "explicit"})

    assert app.config["SECRET_KEY"] == "explicit"


def test_first_run_requires_and_persists_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("INBOX_PATH", "/ignored/inbox")
    monkeypatch.setenv("LIBRARY_PATH", "/ignored/library")
    monkeypatch.setenv("NAVIDROME_RESCAN_URL", "https://ignored.invalid/scan")
    state_path = tmp_path / "config"
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "STATE_PATH": str(state_path), "BROWSE_ROOTS": [str(tmp_path)]})
    client = app.test_client()

    assert client.get("/").headers["Location"].endswith("/setup")
    unavailable = client.get("/api/inbox")
    assert unavailable.status_code == 503
    assert unavailable.json["setup"] == "/setup"

    inbox = tmp_path / "chosen-inbox"
    library = tmp_path / "chosen-library"
    saved = client.post("/setup", data={
        "inbox_path": str(inbox),
        "library_path": str(library),
        "navidrome_rescan_url": "https://music.example.test/scan",
        "navidrome_token": "secret-token",
    })
    assert saved.status_code == 302
    assert saved.headers["Location"] == "/"
    assert inbox.is_dir()
    assert library.is_dir()

    restarted = create_app({"TESTING": True, "SECRET_KEY": "test", "STATE_PATH": str(state_path), "BROWSE_ROOTS": [str(tmp_path)]})
    assert restarted.config["INBOX_PATH"] == str(inbox)
    assert restarted.config["LIBRARY_PATH"] == str(library)
    assert restarted.config["NAVIDROME_RESCAN_URL"] == "https://music.example.test/scan"
    assert restarted.config["NAVIDROME_TOKEN"] == "secret-token"
    assert restarted.test_client().get("/").status_code == 200


def test_browse_is_limited_to_configured_mount_roots(tmp_path):
    mounted = tmp_path / "mounted"
    (mounted / "inbox").mkdir(parents=True)
    (mounted / "library").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "STATE_PATH": str(tmp_path / "config"),
        "BROWSE_ROOTS": [str(mounted)],
    })
    client = app.test_client()
    root = client.get("/api/browse")
    assert root.status_code == 200
    assert {entry["name"] for entry in root.json["entries"]} == {"inbox", "library"}
    assert client.get(f"/api/browse?path={outside}").status_code == 400


def test_browse_exposes_and_navigates_each_mounted_root(tmp_path):
    first = tmp_path / "first-mount"
    second = tmp_path / "userMedia"
    (first / "inbox").mkdir(parents=True)
    (second / "music" / "albums").mkdir(parents=True)
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "STATE_PATH": str(tmp_path / "config"),
        "BROWSE_ROOTS": [str(first), str(second)],
    })
    client = app.test_client()

    root = client.get("/api/browse")
    assert root.json["roots"] == [
        {"name": "first-mount", "path": str(first)},
        {"name": "userMedia", "path": str(second)},
    ]
    other_root = client.get("/api/browse", query_string={"path": str(second)})
    assert other_root.status_code == 200
    assert other_root.json["path"] == str(second)
    assert other_root.json["parent"] is None
    assert other_root.json["entries"] == [{"name": "music", "path": str(second / "music")}]


def test_setup_uses_compact_logo_typeable_paths_and_modal_browser(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "STATE_PATH": str(tmp_path / "config"),
        "BROWSE_ROOTS": [str(tmp_path)],
    })
    client = app.test_client()

    page = client.get("/setup")
    assert b'src="/static/cratekeep-logo.png"' in page.data
    assert b'.logo { display: block; width: 112px;' in page.data
    assert b'id="inbox_path"' in page.data
    assert b'readonly' not in page.data
    assert b'role="dialog" aria-modal="true"' in page.data
    assert b'id="browser-breadcrumbs"' in page.data
    assert b'id="browser-search"' in page.data
    assert b'id="browser-path-form"' in page.data
    assert b"event.key === 'Escape'" in page.data
    assert b"fetch('/api/browse?path='" in page.data
    logo = client.get("/static/cratekeep-logo.png")
    assert logo.status_code == 200
    assert logo.mimetype == "image/png"


def test_settings_update_paths_and_preserve_or_clear_token(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    client.post("/settings", data={
        "inbox_path": str(tmp_path / "inbox"),
        "library_path": str(tmp_path / "library"),
        "navidrome_token": "saved-token",
    })

    new_inbox = tmp_path / "new-inbox"
    client.post("/settings", data={
        "inbox_path": str(new_inbox),
        "library_path": str(tmp_path / "library"),
        "navidrome_token": "",
    })
    assert app.config["INBOX_PATH"] == str(new_inbox)
    assert app.config["NAVIDROME_TOKEN"] == "saved-token"
    assert f'directory: "{tmp_path / "library"}"' in Path(app.config["BEETS_CONFIG"]).read_text()

    client.post("/settings", data={
        "inbox_path": str(new_inbox),
        "library_path": str(tmp_path / "library"),
        "navidrome_token": "",
        "clear_navidrome_token": "1",
    })
    assert app.config["NAVIDROME_TOKEN"] == ""


def test_review_then_import(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    album = tmp_path / "inbox" / "album"
    album.mkdir()
    (album / "song.mp3").write_bytes(b"not-real-audio")
    client = app.test_client()

    preview = client.post("/api/imports/preview", json={"path": "album"})
    assert preview.status_code == 201
    assert preview.json["files"] == ["album/song.mp3"]

    class Result:
        returncode = 0
        stdout = "Imported\n"
        stderr = ""

    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return Result()

    monkeypatch.setattr("beets_mvp.subprocess.run", fake_run)
    executed = client.post(f"/api/imports/{preview.json['id']}/execute")
    assert executed.status_code == 200
    assert executed.json["status"] == "complete"
    assert seen["command"][-2:] == ["--move", str(album)]


def test_rejects_path_escape(tmp_path):
    client = make_app(tmp_path).test_client()
    response = client.post("/api/imports/preview", json={"path": "../outside"})
    assert response.status_code == 400
