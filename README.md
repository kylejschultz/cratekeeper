# Cratekeeper

Cratekeeper is a lightweight web interface for managing a [beets](https://beets.io/) music library. It lists music waiting in an inbox, provides a review-and-execute import flow, edits common metadata and file tags, and can request a Navidrome rescan.

> **Security:** Cratekeeper does not include authentication. Run it only on a trusted network or behind an authenticating reverse proxy. Do not expose it directly to the public internet.

## Quick start with Docker Compose

Requirements: Docker Engine with the Compose plugin.

```sh
git clone https://github.com/kylejschultz/cratekeeper.git
cd cratekeeper
mkdir -p data/inbox data/library data/state
APP_UID="$(id -u)" APP_GID="$(id -g)" docker compose up -d
```

Open <http://localhost:8000>. Put albums or individual audio files in `data/inbox`. Imported files are moved to `data/library`; application and beets databases are stored in `data/state`.

The compose file uses `ghcr.io/kylejschultz/cratekeeper:latest` and also includes a local build definition. To build from your checkout, run `docker compose up -d --build`. On systems where `id` is unavailable, Compose defaults to UID/GID 1000.

## Unraid

Create an **Add Container** entry with these settings:

- **Repository:** `ghcr.io/kylejschultz/cratekeeper:latest`
- **Web UI:** `http://[IP]:[PORT:8000]/`
- **Port:** container `8000`, mapped to a host port of your choice
- **Paths:**
  - `/data/inbox` → an inbox share
  - `/data/library` → your music library share
  - `/data/state` → `/mnt/user/appdata/cratekeeper`
- **Variables:** set `SECRET_KEY` to a long random value; optionally configure the Navidrome variables below

The container runs as UID 10001 by default. Ensure the mapped directories are writable by that UID, or use Unraid's container advanced settings to set an appropriate numeric user such as `99:100`.

## Local development

Python 3.11 or newer is recommended.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
mkdir -p data/inbox data/library data/state
flask --app beets_mvp run --debug
```

For a production-like local process:

```sh
gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 4 'beets_mvp:create_app()'
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `INBOX_PATH` | `./data/inbox` | Music staging directory. |
| `LIBRARY_PATH` | `./data/library` | Managed beets music library. |
| `STATE_PATH` | `./data/state` | SQLite databases and generated beets config. |
| `BEETS_CONFIG` | `$STATE_PATH/config.yaml` | Existing beets YAML config; a minimal config is generated when absent. |
| `SECRET_KEY` | development-only value | Flask signing key. Set a random value for normal use. |
| `NAVIDROME_RESCAN_URL` | unset | Full URL that accepts a POST request to trigger scanning. |
| `NAVIDROME_TOKEN` | unset | Optional bearer token sent to the rescan URL. |

## API overview

Imports require a preview followed by an explicit execute call:

```sh
curl -sS -X POST http://localhost:8000/api/imports/preview \
  -H 'Content-Type: application/json' -d '{"path":"My Album"}'
curl -sS -X POST http://localhost:8000/api/imports/1/execute
```

Execution verifies that the previewed files have not changed, then runs `beet import --quiet --noautotag --move`.

- `GET /healthz` — liveness check
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
- The generated beets config is not overwritten. If storage paths change, update or remove the state config intentionally.
