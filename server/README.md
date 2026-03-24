# IFC Processing Server

This server ingests IFC or JSON component data, stores the result in the configured backend, and exposes HTTP endpoints for querying models, entities, component GUIDs, validation results, GitHub-hosted IFC metadata, and indexed summaries.

## What the server does

- Accepts `.ifc` and `.json` uploads.
- Converts IFC files to component JSON using `ingestors/ifc4ingestor.py`.
- Stores components in the configured backend.
- Serves model data through HTTP APIs.
- Supports git-backed version lookup for historical queries when git versioning is configured.
- Can fetch IFC revisions directly from GitHub for diff, validation, and summary workflows.
- Can cache validation JSON/BCF outputs and preindexed IFC summaries.

## Run the server

From the `server` directory:

```bash
python server.py
```

Common options:

```bash
python server.py --backend fileBased --port 5001
python server.py --backend mongodbBased --port 5001
python server.py --debug
```

Defaults:

- Backend: `fileBased`
- Host: `0.0.0.0`
- Port: `5001`

## Environment configuration

The server loads `.env` values automatically from either:

- `server/.env`
- repo-root `.env`

Existing process environment variables take precedence over `.env` values.

A template is provided in [`.env.example`](.env.example).

### Supported environment variables

- `GIT_PUSH_REMOTE_URL`: remote repo URL to push model-version commits to.
- `GIT_PUSH_BRANCH`: branch to push to.
- `GITHUB_TOKEN`: GitHub token used for HTTPS push and authenticated GitHub API/file access.
- `VALIDATION_TOKEN`: buildingSMART validation API token used by `/api/validate`.
- `GIT_USER_NAME`: git commit author name.
- `GIT_USER_EMAIL`: git commit author email.
- `VERSION_REPO_ROOT`: local git repo used to resolve `version=` queries.
- `VERSION_DATA_REL_PATH`: path inside the version repo that contains model data.
- `PREINDEX_STORAGE_BACKEND`: indexed artifact backend, one of `filesystem`, `blob`, or `auto`.
- `PREINDEX_ARTIFACTS_PATH`: local filesystem path for indexed artifacts.
- `PREINDEX_BLOB_PREFIX`: blob prefix for indexed artifacts when blob storage is enabled.
- `PREINDEX_AUTOSTART`: whether tracked-file preindexing starts automatically on boot.
- `PREINDEX_MANIFEST_PATH`: JSON manifest path that lists tracked GitHub IFC files/sets.
- `PREINDEX_MANIFEST_JSON`: inline JSON manifest override.
- `PREINDEX_MAX_WORKERS`: worker count for background preindex jobs.
- `BLOB_READ_WRITE_TOKEN`: Vercel Blob token for durable indexed artifacts and validation result storage.

## Web pages

### Admin page

URL:

```text
http://localhost:5001/
```

The admin page is served by [`templates/admin.html`](templates/admin.html).

What it supports:

- Upload IFC or JSON files.
- Overwrite an existing model when names collide.
- View models currently in the file-based store.
- Refresh the model list.
- Delete one or more selected models.
- View the latest server version status.
- See the returned git `versionId` after an upload when git versioning is enabled.

Upload behavior:

- `.ifc` files are converted to JSON components before storage.
- `.json` files must contain an array of component objects.
- For `fileBased`, each model name maps to a directory under `dataStores/fileBased/data`.

### Viewer page

URL:

```text
http://localhost:5001/viewer
```

This serves the advanced viewer template.

## GitHub-backed workflows

Several endpoints work directly against GitHub-hosted IFC files instead of the local store.

Supported request styles:

- Explicit repo coordinates: `repoOwner`, `repoName`, `commitSha` or `ref`, and `filePath`
- GitHub URL: `https://github.com/<owner>/<repo>/blob/<sha>/<path>` or `https://raw.githubusercontent.com/...`

These routes use the GitHub token from the request payload when supplied, otherwise `GITHUB_TOKEN`.

## Validation and cached artifacts

`POST /api/validate` fetches an IFC from GitHub, submits it to the buildingSMART validation API, polls until completion, summarizes the result, and caches both the JSON summary and BCF output.

Behavior:

- Cached validation results are returned immediately on repeated requests for the same `commitSha + filePath`.
- `?format=bcf` returns a downloadable `.bcf` file instead of JSON.
- Validation artifacts are stored in local `server/validation_results` unless `BLOB_READ_WRITE_TOKEN` is configured, in which case they are stored in Vercel Blob.

The summarized JSON includes top-level pass/fail booleans for:

- `schema`
- `syntax`
- `normative`
- `industry_practices`

## Demo preindexing

Tracked GitHub IFC files can be preindexed in the background so the frontend can query compact component summaries without reparsing IFC files on demand.

Behavior:

- The tracked set is defined by `preindex_manifest.json`, `PREINDEX_MANIFEST_PATH`, or `PREINDEX_MANIFEST_JSON`.
- Local runtimes autostart preindexing by default.
- Vercel runtimes default to read/query mode and disable autostart unless explicitly enabled.
- `GET /api/versions` prefers preindexed versions when available and falls back to git history otherwise.

## Versioned queries

Most read endpoints support an optional:

- `version=<git-sha>`

Behavior:

- If `version` is omitted, the latest in-memory data is used.
- If `version` is provided, the server reconstructs model data from that git commit.
- Valid values are git commit SHAs recognized by the configured version repo.
- You can inspect available versions with `GET /api/versions`.

Example:

```text
http://localhost:5001/api/components?models=HelloWall&version=07fbe7b
```

## Query conventions

Several endpoints accept comma-separated filter values.

Examples:

```text
models=HelloWall,HelloWall-01
entityTypes=IfcWall,IfcSlab
entityGuids=guid-a,guid-b
componentTypes=IfcWallComponent,IfcDoorComponent
componentGuids=guid-1,guid-2
```

Notes:

- `models` filters by model directory / model name.
- `entityTypes` filters by IFC entity type.
- `entityGuids` filters by source entity GUIDs.
- `componentGuids` filters by component GUIDs.
- `componentTypes` accepts names with or without the `Component` suffix. Descendants are expanded when possible.

## API endpoints

### `GET /api/status`

Returns basic server status.

Response fields:

- `status`
- `data_store`
- `timestamp`
- `version`
- `latestVersion` when git versioning is available
- `latestIndexedVersion` when a preindexed artifact is ready
- `storageMode`, `indexedStorageBackend`, `indexedStorageDurable`, `modelManagementMode`, `indexingMode`
- `preindex` with tracked-file/artifact status details

Example:

```text
http://localhost:5001/api/status
```

### `GET /api/stores`

Lists the available store types and which one is active.

Example:

```text
http://localhost:5001/api/stores
```

### `GET /api/preindex/status`

Returns demo preindex readiness plus the latest tracked artifact state.

Query parameters:

- `limit` optional, default `25`

Example:

```text
http://localhost:5001/api/preindex/status
```

### `POST /api/preindex/trigger`

Queues tracked IFC files for background preindexing.

JSON body:

```json
{
  "force": true
}
```

Behavior:

- Returns `501` if preindexing is not configured.
- Requires model management to be enabled for the runtime.

### `POST /api/upload`

Uploads and processes an IFC or JSON file.

Query parameters:

- `overwrite=true|false`

Form fields:

- `file`: uploaded `.ifc` or `.json`

Behavior:

- Returns `409` if the model already exists and overwrite is not enabled.
- Returns `versionId` when git versioning is enabled and a commit was created.

Example using `curl`:

```bash
curl -X POST -F "file=@HelloWall.ifc" "http://localhost:5001/api/upload"
```

Overwrite example:

```bash
curl -X POST -F "file=@HelloWall.ifc" "http://localhost:5001/api/upload?overwrite=true"
```

### `GET /api/github/branches`

Lists branches for a GitHub repository.

Query parameters:

- `repoOwner` required
- `repoName` required
- `perPage` optional, default `20`

### `GET /api/github/commits`

Lists commits for a GitHub ref.

Query parameters:

- `repoOwner` required
- `repoName` required
- `ref` required
- `perPage` optional, default `35`

### `GET /api/github/file-history`

Lists commits that touched one file path.

Query parameters:

- `repoOwner` required
- `repoName` required
- `ref` required
- `filePath` required
- `perPage` optional, default `20`

### `GET /api/github/tree`

Returns a recursive tree for a GitHub ref.

Query parameters:

- `repoOwner` required
- `repoName` required
- `ref` required

### `GET /api/github/file`

Streams one GitHub-hosted file through the backend.

Query parameters:

- `repoOwner` required
- `repoName` required
- `ref` required
- `filePath` required

### `GET /api/github/components`

Returns compact component metadata from a GitHub-hosted IFC file.

Query parameters:

- `repoOwner` required
- `repoName` required
- `ref` required
- `filePath` required
- `guids` optional comma-separated GlobalIds

Behavior:

- Uses a preindexed summary when available.
- Falls back to parsing the IFC file on demand when no indexed artifact exists.

### `POST /api/ifc/diff`

Compares two GitHub-hosted IFC revisions and returns diff metadata.

JSON body:

```json
{
  "current": {
    "repoOwner": "OpenBIM-Hackathon-Team-ECS-Version",
    "repoName": "Sample-IFC-Files",
    "commitSha": "abc1234",
    "filePath": "models/Building.ifc"
  },
  "last": {
    "repoOwner": "OpenBIM-Hackathon-Team-ECS-Version",
    "repoName": "Sample-IFC-Files",
    "commitSha": "def5678",
    "filePath": "models/Building.ifc"
  }
}
```

Each side can also use `githubUrl` instead of explicit repo coordinates.

### `POST /api/validate`

Runs end-to-end validation for a GitHub-hosted IFC file.

JSON body:

```json
{
  "repoOwner": "OpenBIM-Hackathon-Team-ECS-Version",
  "repoName": "Sample-IFC-Files",
  "commitSha": "abc1234",
  "filePath": "models/Building.ifc"
}
```

Alternative body:

```json
{
  "githubUrl": "https://github.com/OpenBIM-Hackathon-Team-ECS-Version/Sample-IFC-Files/blob/abc1234/models/Building.ifc"
}
```

Query parameters:

- `format=json|bcf` optional, default `json`

Behavior:

- Returns `400` for missing repo/file fields or invalid GitHub URLs.
- Returns cached results when available.
- Returns a binary BCF attachment when `format=bcf`.

### `GET /api/models`

Returns the list of model names.

Query parameters:

- `version` optional

Examples:

```text
http://localhost:5001/api/models
http://localhost:5001/api/models?version=07fbe7b
```

### `GET /api/models/details`

Returns model metadata for the file-based backend.

This endpoint is only available for `fileBased`.

Example:

```text
http://localhost:5001/api/models/details
```

### `POST /api/models/delete`

Deletes one or more models.

This endpoint is only available for `fileBased`.

JSON body:

```json
{
  "models": ["HelloWall", "HelloWall-01"]
}
```

You can also send:

```json
{
  "model": "HelloWall"
}
```

Example:

```bash
curl -X POST "http://localhost:5001/api/models/delete" \
  -H "Content-Type: application/json" \
  -d '{"models":["HelloWall"]}'
```

### `POST /api/refresh`

Refreshes the in-memory index from the current store.

Example:

```bash
curl -X POST "http://localhost:5001/api/refresh"
```

### `GET /api/entityTypes`

Returns entity types present in the selected models.

Query parameters:

- `models` optional
- `version` optional

Examples:

```text
http://localhost:5001/api/entityTypes
http://localhost:5001/api/entityTypes?models=HelloWall
http://localhost:5001/api/entityTypes?models=HelloWall&version=07fbe7b
```

### `GET /api/componentTypes`

Returns component types present in the selected models.

Query parameters:

- `models` optional
- `version` optional

Examples:

```text
http://localhost:5001/api/componentTypes
http://localhost:5001/api/componentTypes?models=HelloWall
```

### `GET /api/entityGuids`

Returns entity GUIDs grouped by model.

Query parameters:

- `models` optional
- `entityTypes` optional
- `version` optional

Examples:

```text
http://localhost:5001/api/entityGuids?models=HelloWall
http://localhost:5001/api/entityGuids?models=HelloWall&entityTypes=IfcWall
http://localhost:5001/api/entityGuids?models=HelloWall&entityTypes=IfcWall&version=07fbe7b
```

### `GET /api/componentGuids`

Returns component GUIDs grouped by model.

Query parameters:

- `models` optional
- `entityGuids` optional
- `entityTypes` optional
- `componentTypes` optional
- `version` optional

Behavior:

- If `componentTypes` is provided, component type filtering is used first.
- Otherwise, entity-based filtering is used.

Examples:

```text
http://localhost:5001/api/componentGuids?models=HelloWall
http://localhost:5001/api/componentGuids?models=HelloWall&entityTypes=IfcWall
http://localhost:5001/api/componentGuids?models=HelloWall&componentTypes=IfcWallComponent
http://localhost:5001/api/componentGuids?models=HelloWall&entityGuids=933c4a06-93b8-11d3-80f8-00c04f8efc2c
```

### `GET /api/components`

Returns full component payloads grouped by model.

Query parameters:

- `componentGuids` optional
- `models` optional
- `entityTypes` optional
- `entityGuids` optional
- `componentTypes` optional
- `version` optional

Filter precedence:

1. `componentGuids`
2. `componentTypes`
3. `models` / `entityTypes` / `entityGuids`
4. no filters means all components from all models

Examples:

```text
http://localhost:5001/api/components?models=HelloWall
http://localhost:5001/api/components?models=HelloWall&version=07fbe7b
http://localhost:5001/api/components?models=HelloWall&entityTypes=IfcWall
http://localhost:5001/api/components?models=HelloWall&componentTypes=IfcWallComponent
http://localhost:5001/api/components?componentGuids=af139d38-0415-4c92-32bc-cde97debca24
```

### `GET /api/versions`

Returns recent git-backed model versions.

Query parameters:

- `limit` optional, default `50`

Response shape:

```json
{
  "latest": "<full_sha>",
  "source": "preindex",
  "versions": [
    {
      "versionId": "<full_sha>",
      "shortId": "07fbe7b",
      "message": "HelloWall-03",
      "timestamp": "2026-03-22T10:40:31-04:00",
      "author": "Deployment Bot"
    }
  ]
}
```

Examples:

```text
http://localhost:5001/api/versions
http://localhost:5001/api/versions?limit=10
```

## Common workflows

### Upload a model from the admin page

1. Open `http://localhost:5001/`.
2. Drag and drop or select an IFC or JSON file.
3. Confirm overwrite if the model name already exists.
4. Wait for the success panel.
5. Copy the returned `versionId` if you want to query that exact snapshot later.

### Get all components for a model

```text
http://localhost:5001/api/components?models=HelloWall
```

### Get the same model at a historical version

1. Find a valid version from `/api/versions`.
2. Query with `version=<short-or-full-sha>`.

Example:

```text
http://localhost:5001/api/components?models=HelloWall&version=07fbe7b
```

### Find walls only

```text
http://localhost:5001/api/components?models=HelloWall&entityTypes=IfcWall
```

### Find components from a known entity GUID

```text
http://localhost:5001/api/componentGuids?models=HelloWall&entityGuids=<entity-guid>
```

## Error cases

Common API errors:

- `400 Bad Request`: invalid filter value, invalid version, or malformed request.
- `409 Conflict`: upload model already exists and overwrite is not enabled.
- `413 Payload Too Large`: upload exceeded 500 MB.
- `501 Not Implemented`: endpoint not available for the active backend.
- `502+`: upstream GitHub or validation service failure.

Version-specific errors:

- `Unknown or ambiguous version: '<sha>'`: the SHA is not present in the configured version repo.
- `Invalid version format`: the value is not a valid 7 to 40 character hex SHA.

## Backend notes

### `fileBased`

- Stores one directory per model under `dataStores/fileBased/data`.
- Stores one JSON file per component.
- Supports `/api/models/details` and `/api/models/delete`.
- Supports git-backed historical version queries when configured.

### `mongodbBased`

- Uses the MongoDB store implementation.
- Does not support the file-based model details and delete endpoints.

## Relevant files

- [`server.py`](server.py)
- [`templates/admin.html`](templates/admin.html)
- [`git_versioning.py`](git_versioning.py)
- [`validation_service.py`](validation_service.py)
- [`.env.example`](.env.example)
