from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from beets import config as beets_config
from beets.library import Library
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for

AUDIO_EXTENSIONS = {".aac", ".aiff", ".alac", ".ape", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wv"}
EDITABLE_FIELDS = {"title", "artist", "album", "albumartist", "genre", "year", "track", "disc"}


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "local-development-only"),
        INBOX_PATH=os.getenv("INBOX_PATH", str(Path.cwd() / "data/inbox")),
        LIBRARY_PATH=os.getenv("LIBRARY_PATH", str(Path.cwd() / "data/library")),
        STATE_PATH=os.getenv("STATE_PATH", str(Path.cwd() / "data/config")),
        NAVIDROME_RESCAN_URL=os.getenv("NAVIDROME_RESCAN_URL", ""),
        NAVIDROME_TOKEN=os.getenv("NAVIDROME_TOKEN", ""),
    )
    if test_config:
        app.config.update(test_config)

    for key in ("INBOX_PATH", "LIBRARY_PATH", "STATE_PATH"):
        Path(app.config[key]).mkdir(parents=True, exist_ok=True)
    state_path = Path(app.config["STATE_PATH"])
    app.config["BEETS_DB"] = str(state_path / "library.db")
    app.config["APP_DB"] = str(state_path / "app.db")
    app.config["BEETS_CONFIG"] = os.getenv("BEETS_CONFIG", str(state_path / "config.yaml"))
    _write_beets_config(app)
    # Point the Python API at the same explicit config as the CLI. Reading
    # without user discovery avoids writing under ~/.config in containers.
    beets_config.set_file(app.config["BEETS_CONFIG"])
    beets_config.read(user=False, defaults=True)
    _init_db(app.config["APP_DB"])

    @app.get("/")
    def index():
        return render_template("index.html", candidates=_inbox_candidates(app), items=_items(app))

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    @app.get("/api/inbox")
    def inbox():
        return jsonify(_inbox_candidates(app))

    @app.get("/api/items")
    def items():
        return jsonify(_items(app))

    @app.post("/api/imports/preview")
    def preview_import():
        relative = _request_value("path")
        source = _safe_inbox_path(app, relative)
        files = _audio_files(source)
        if not files:
            abort(400, "selection contains no supported audio files")
        file_names = [str(p.relative_to(Path(app.config["INBOX_PATH"]).resolve())) for p in files]
        with _connect(app.config["APP_DB"]) as db:
            cursor = db.execute(
                "INSERT INTO import_jobs(source, files_json, status, created_at) VALUES (?, ?, 'review', ?)",
                (relative, json.dumps(file_names), _now()),
            )
            job_id = cursor.lastrowid
        return jsonify(id=job_id, source=relative, files=file_names, status="review"), 201

    @app.post("/api/imports/<int:job_id>/execute")
    def execute_import(job_id: int):
        with _connect(app.config["APP_DB"]) as db:
            job = db.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                abort(404)
            if job["status"] != "review":
                abort(409, "job has already been executed")
            source = _safe_inbox_path(app, job["source"])
            expected = json.loads(job["files_json"])
            root = Path(app.config["INBOX_PATH"]).resolve()
            current = [str(p.relative_to(root)) for p in _audio_files(source)]
            if current != expected:
                abort(409, "inbox contents changed; create a new preview")
            command = ["beet", "-c", app.config["BEETS_CONFIG"], "import", "--quiet", "--noautotag", "--move", str(source)]
            command_env = os.environ.copy()
            command_env["BEETSDIR"] = app.config["STATE_PATH"]
            result = subprocess.run(command, text=True, capture_output=True, timeout=3600, check=False, env=command_env)
            output = (result.stdout + result.stderr)[-12000:]
            status = "complete" if result.returncode == 0 else "failed"
            db.execute(
                "UPDATE import_jobs SET status = ?, output = ?, finished_at = ? WHERE id = ?",
                (status, output, _now(), job_id),
            )
        return jsonify(id=job_id, status=status, returncode=result.returncode, output=output), (200 if result.returncode == 0 else 502)

    @app.patch("/api/items/<int:item_id>")
    @app.post("/api/items/<int:item_id>")
    def edit_item(item_id: int):
        values = request.get_json(silent=True) or request.form.to_dict()
        unknown = set(values) - EDITABLE_FIELDS
        if unknown:
            abort(400, f"unsupported fields: {', '.join(sorted(unknown))}")
        library = _library(app)
        item = library.get_item(item_id)
        if item is None:
            abort(404)
        for field, value in values.items():
            if field in {"year", "track", "disc"}:
                try:
                    value = int(value or 0)
                except (TypeError, ValueError):
                    abort(400, f"{field} must be an integer")
            item[field] = value
        item.store()
        try:
            item.write()
        except Exception as exc:
            abort(500, f"database updated but tag write failed: {exc}")
        if request.is_json:
            return jsonify(_serialize_item(item))
        flash("Metadata and file tags updated.")
        return redirect(url_for("index"))

    @app.post("/api/navidrome/rescan")
    def navidrome_rescan():
        target = app.config["NAVIDROME_RESCAN_URL"]
        if not target:
            abort(503, "NAVIDROME_RESCAN_URL is not configured")
        headers = {"Accept": "application/json"}
        if app.config["NAVIDROME_TOKEN"]:
            headers["Authorization"] = f"Bearer {app.config['NAVIDROME_TOKEN']}"
        upstream = urllib.request.Request(target, data=b"", headers=headers, method="POST")
        try:
            with urllib.request.urlopen(upstream, timeout=15) as response:
                body = response.read(4096).decode("utf-8", "replace")
                return jsonify(status="requested", upstream_status=response.status, response=body)
        except urllib.error.URLError as exc:
            return jsonify(status="failed", error=str(exc)), 502

    return app


def _write_beets_config(app: Flask) -> None:
    path = Path(app.config["BEETS_CONFIG"])
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join([
        f"directory: {json.dumps(str(Path(app.config['LIBRARY_PATH']).resolve()))}",
        f"library: {json.dumps(str(Path(app.config['BEETS_DB']).resolve()))}",
        "import:",
        "  move: true",
        "  write: true",
        "  autotag: false",
        "  resume: false",
        "plugins: []",
        "",
    ])
    path.write_text(content, encoding="utf-8")


def _init_db(path: str) -> None:
    with _connect(path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS import_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            files_json TEXT NOT NULL,
            status TEXT NOT NULL,
            output TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            finished_at TEXT
        )""")


def _connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def _library(app: Flask) -> Library:
    return Library(app.config["BEETS_DB"], directory=app.config["LIBRARY_PATH"])


def _items(app: Flask) -> list[dict]:
    return [_serialize_item(item) for item in _library(app).items()]


def _serialize_item(item) -> dict:
    result = {key: item.get(key) for key in ("id", "title", "artist", "album", "albumartist", "genre", "year", "track", "disc")}
    path = item.get("path")
    result["path"] = os.fsdecode(path) if path else ""
    return result


def _inbox_candidates(app: Flask) -> list[dict]:
    root = Path(app.config["INBOX_PATH"]).resolve()
    candidates = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith(".") or (entry.is_file() and entry.suffix.lower() not in AUDIO_EXTENSIONS):
            continue
        files = _audio_files(entry)
        if files:
            candidates.append({"path": entry.name, "files": len(files), "bytes": sum(p.stat().st_size for p in files)})
    return candidates


def _audio_files(path: Path) -> list[Path]:
    iterator = path.rglob("*") if path.is_dir() else [path]
    return sorted((p.resolve() for p in iterator if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS), key=str)


def _safe_inbox_path(app: Flask, relative: str) -> Path:
    if not relative:
        abort(400, "path is required")
    root = Path(app.config["INBOX_PATH"]).resolve()
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents or not candidate.exists():
        abort(400, "path must identify an existing selection inside the inbox")
    return candidate


def _request_value(name: str) -> str:
    values = request.get_json(silent=True) or request.form
    return str(values.get(name, ""))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
