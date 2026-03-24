# ECS-Version Backend

Backend server for the **openBIM Hackathon 2026** ECS-Version project. It ingests IFC building models, decomposes them into Entity-Component-System (ECS) JSON components, and serves them through a REST API with optional git-backed versioning.

**Frontend:** [OpenBIM-Hackathon-Team-ECS-Version/frontend](https://github.com/OpenBIM-Hackathon-Team-ECS-Version/frontend)

## Features

- Upload and process `.ifc` and `.json` model files
- IFC-to-ECS component decomposition via IfcOpenShell
- Pluggable storage backends (file-based, MongoDB)
- Git-backed model versioning with historical queries
- REST API for querying models, entities, components, and versions
- Admin UI for uploads and model management
- IFC diff service for comparing model versions

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
| `GET` | `/api/models` | List model names |
| `GET` | `/api/models/details` | Model metadata (file-based only) |
| `POST` | `/api/upload` | Upload an IFC or JSON file |
| `POST` | `/api/models/delete` | Delete models (file-based only) |
| `POST` | `/api/refresh` | Refresh in-memory index |
| `GET` | `/api/entityTypes` | Entity types in selected models |
| `GET` | `/api/componentTypes` | Component types in selected models |
| `GET` | `/api/entityGuids` | Entity GUIDs by model |
| `GET` | `/api/componentGuids` | Component GUIDs by model |
| `GET` | `/api/components` | Full component payloads |
| `GET` | `/api/versions` | Git-backed version history |
| `GET` | `/api/stores` | Available store backends |

Most read endpoints support optional `version=<git-sha>` and `models=` query parameters. See `server/README.md` for full API documentation.

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
| `GIT_USER_NAME` | Git commit author name |
| `GIT_USER_EMAIL` | Git commit author email |
| `VERSION_REPO_ROOT` | Local git repo for version queries |
| `VERSION_DATA_REL_PATH` | Data path inside the version repo |

See `server/.env.example` for a template.

## License

[MIT](LICENSE)
