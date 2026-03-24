# ECS-Version Backend

Backend server for the **openBIM Hackathon 2026** ECS-Version project. It ingests IFC building models, decomposes them into Entity-Component-System (ECS) JSON components, and serves them through a REST API with optional git-backed versioning.

**Frontend:** [OpenBIM-Hackathon-Team-ECS-Version/frontend](https://github.com/OpenBIM-Hackathon-Team-ECS-Version/frontend)

## Features

- Upload and process `.ifc` and `.json` model files
- IFC-to-ECS component decomposition via IfcOpenShell
- Pluggable storage backends (file-based, MongoDB)
- Git-backed model versioning with historical queries
- GitHub-backed IFC fetch, commit history, tree, and file proxy endpoints
- IFC diff service for comparing two GitHub revisions
- IFC validation via the buildingSMART validation API with cached JSON/BCF output
- Demo preindexing for tracked GitHub IFC files with reusable indexed summaries
- REST API for querying models, entities, components, and versions
- Admin UI for uploads and model management
- Viewer UI for query and comparison workflows

## Tech Stack

- **Python 3** / **Flask**
- **IfcOpenShell** for IFC parsing
- **Flask-CORS** for cross-origin support
- File-based or MongoDB storage

## Quick Start

```bash
git clone https://github.com/OpenBIM-Hackathon-Team-ECS-Version/backend.git
cd backend

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp server/.env.example server/.env
# Edit server/.env with your settings

cd server
python server.py
```

The server starts at `http://localhost:5001` by default.

### Server Options

```bash
python server.py --backend fileBased --port 5001   # default
python server.py --backend mongodbBased --port 5001
python server.py --debug
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/status` | Server status and version info |
| `GET` | `/api/preindex/status` | Preindex readiness and artifact state |
| `POST` | `/api/preindex/trigger` | Queue tracked IFCs for preindexing |
| `GET` | `/api/models` | List model names |
| `GET` | `/api/models/details` | Model metadata (file-based only) |
| `POST` | `/api/upload` | Upload an IFC or JSON file |
| `POST` | `/api/models/delete` | Delete models (file-based only) |
| `POST` | `/api/refresh` | Refresh in-memory index |
| `GET` | `/api/github/branches` | List GitHub branches for a repo |
| `GET` | `/api/github/commits` | List commits for a ref |
| `GET` | `/api/github/file-history` | List commits for one file path |
| `GET` | `/api/github/tree` | Fetch a recursive GitHub tree |
| `GET` | `/api/github/file` | Stream a GitHub-hosted file |
| `GET` | `/api/github/components` | Summarize components from a GitHub IFC |
| `POST` | `/api/ifc/diff` | Compare two GitHub-hosted IFC revisions |
| `POST` | `/api/validate` | Validate a GitHub-hosted IFC and optionally return BCF |
| `GET` | `/api/entityTypes` | Entity types in selected models |
| `GET` | `/api/componentTypes` | Component types in selected models |
| `GET` | `/api/entityGuids` | Entity GUIDs by model |
| `GET` | `/api/componentGuids` | Component GUIDs by model |
| `GET` | `/api/components` | Full component payloads |
| `GET` | `/api/versions` | Git-backed version history |
| `GET` | `/api/stores` | Available store backends |

Most read endpoints support optional `version=<git-sha>` and `models=` query parameters. GitHub-backed endpoints accept either explicit repo coordinates or a GitHub blob/raw URL. See [`server/README.md`](server/README.md) for the full API surface.

## Project Structure

```
backend/
├── index.py                  # App entry point (e.g. for Vercel)
├── requirements.txt          # Python dependencies
├── server/
│   ├── server.py             # Flask app and API routes
│   ├── git_versioning.py     # Git-backed version management
│   ├── ifc_diff_service.py   # IFC model diff/comparison
│   ├── preindex_service.py   # Pre-indexing service
│   ├── indexed_artifacts.py  # Indexed artifact management
│   ├── ingestors/            # IFC-to-ECS conversion
│   ├── dataStores/           # Storage backends
│   ├── templates/            # Admin and viewer HTML
│   └── utils/                # IFC utilities
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GIT_PUSH_REMOTE_URL` | Remote repo URL for model version commits |
| `GIT_PUSH_BRANCH` | Branch to push version commits to |
| `GITHUB_TOKEN` | GitHub token for HTTPS push |
| `VALIDATION_TOKEN` | buildingSMART validation API token |
| `GIT_USER_NAME` | Git commit author name |
| `GIT_USER_EMAIL` | Git commit author email |
| `VERSION_REPO_ROOT` | Local git repo for version queries |
| `VERSION_DATA_REL_PATH` | Data path inside the version repo |
| `PREINDEX_STORAGE_BACKEND` | Indexed artifact storage backend (`filesystem`, `blob`, `auto`) |
| `PREINDEX_ARTIFACTS_PATH` | Local path for indexed artifacts |
| `PREINDEX_BLOB_PREFIX` | Blob key prefix for indexed artifacts |
| `PREINDEX_AUTOSTART` | Enable tracked-file preindexing on startup |
| `PREINDEX_MANIFEST_PATH` | Manifest file path for tracked IFC sets |
| `PREINDEX_MANIFEST_JSON` | Inline JSON manifest for tracked IFC sets |
| `PREINDEX_MAX_WORKERS` | Max background workers for preindexing |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob token for durable indexed/validation artifacts |

See [`server/.env.example`](server/.env.example) for the local template.

## License

[MIT](LICENSE)
