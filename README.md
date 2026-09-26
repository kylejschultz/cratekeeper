# Cratekeeper

Cratekeeper is a lightweight web interface for managing a [beets](https://beets.io/) music library. It lists music waiting in an inbox, provides a review-and-execute import flow, edits common metadata and file tags, and can request a Navidrome rescan.

> **Security:** Cratekeeper does not include authentication. Run it only on a trusted network or behind an authenticating reverse proxy. Do not expose it directly to the public internet.

## Quick start with Docker Compose

Requirements: Docker Engine with the Compose plugin.

```sh
git clone https://github.com/kylejschultz/cratekeeper.git
cd cratekeeper
mkdir -p data/inbox data/library data/config
APP_UID="$(id -u)" APP_GID="$(id -g)" docker compose up -d
```

Open <http://localhost:8788>. Put albums or individual audio files in `data/inbox`. Imported files are moved to `data/library`; application and beets databases are stored in `data/config`.

On first launch, Cratekeeper opens a setup screen. Use **Browse** to choose the mounted inbox and library directories, then optionally enter Navidrome rescan details. The choices are stored in `data/config/app.db` and remain editable from the Settings link. The browser only exposes directories under the container paths you mount; it cannot see arbitrary Unraid host paths.

When upgrading an existing Compose installation, stop the container and move the contents of `data/state` to `data/config` before starting the new image. The database filenames and formats are unchanged.

The compose file uses `ghcr.io/kylejschultz/cratekeeper:latest` and also includes a local build definition. To build from your checkout, run `docker compose up -d --build`. On systems where `id` is unavailable, Compose defaults to UID/GID 1000.

## Unraid

Create an **Add Container** entry with these settings:

- **Repository:** `ghcr.io/kylejschultz/cratekeeper:latest`
- **Web UI:** `http://[IP]:[PORT:8788]/`
- **Port:** container `8788`, mapped to a host port of your choice
- **Paths:**
  - `/data/inbox` → an inbox share
  - `/data/library` → your music library share
  - `/data/config` → `/mnt/user/appdata/cratekeeper`
- **Variables:** set `SECRET_KEY` to a long random value

The container runs as UID 10001 by default. Ensure the mapped directories are writable by that UID, or use Unraid's container advanced settings to set an appropriate numeric user such as `99:100`.

For an Unraid deployment, mount each host directory at a clear container path and choose those container paths in the setup browser. For example, `/mnt/user/media-download-cache/cratekeeper` can map to `/data/inbox`, and `/mnt/user/media-fast/music` can map to `/data/library`. The host paths never need to be entered in Cratekeeper.

## Local development

Python 3.11 or newer is recommended.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
mkdir -p data/inbox data/library data/config
flask --app beets_mvp run --debug
```

Open <http://127.0.0.1:5000> and complete the first-run setup. The form suggests the local `data/inbox` and `data/library` directories.

For a production-like local process:

```sh
gunicorn --bind 127.0.0.1:8788 --workers 1 --threads 4 'beets_mvp:create_app()'
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `STATE_PATH` | `./data/config` | SQLite databases and generated beets config. |
| `SECRET_KEY` | development-only value | Flask signing key. Set a random value for normal use. |

Inbox, library, and optional Navidrome settings are configured in the first-run setup screen and persisted in the application SQLite database. Cratekeeper manages the beets config at `$STATE_PATH/config.yaml` from those settings.

## API overview

Imports require a preview followed by an explicit execute call:

```sh
curl -sS -X POST http://localhost:8788/api/imports/preview \
  -H 'Content-Type: application/json' -d '{"path":"My Album"}'
curl -sS -X POST http://localhost:8788/api/imports/1/execute
```

Execution verifies that the previewed files have not changed, then runs `beet import --quiet --noautotag --move`.

- `GET /healthz` — liveness check
- `GET /api/browse?path=/data` — list directories beneath a mounted container root
- `GET /api/inbox` — list immediate import candidates
- `GET /api/items` — list beets library metadata
- `POST /api/imports/preview` — snapshot an inbox selection for review
- `POST /api/imports/<id>/execute` — execute a reviewed import
- `PATCH /api/items/<id>` — update `title`, `artist`, `album`, `albumartist`, `genre`, `year`, `track`, or `disc`
- `POST /api/navidrome/rescan` — request a scan from the configured endpoint

## Tests

```sh
pytest -q
python -m compileall -q beets_mvp tests
```

## Limitations

- Cratekeeper is designed for one trusted user and one process. It has no authentication, authorization, CSRF protection, job queue, or background workers.
- Imports are synchronous and may occupy the sole Gunicorn worker for up to one hour.
- Imports use existing tags (`--noautotag`); there is no MusicBrainz matching, duplicate-resolution UI, artwork workflow, progress stream, undo, or delete endpoint.
- A metadata database update occurs before its file-tag write, so a failed tag write can leave them temporarily inconsistent. Keep backups and ensure library files are writable.
- Navidrome integration is a generic POST with optional bearer authentication and is not automatically run after imports.
- Updating the library path in Settings also updates the generated beets config.
