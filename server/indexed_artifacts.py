"""Indexed artifact storage with local and Vercel Blob backends."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


_BLOB_API_URL = os.environ.get("VERCEL_BLOB_API_URL", "https://vercel.com/api/blob")
_BLOB_API_VERSION = "12"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _safe_slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value).strip("-") or "artifact"


def _json_bytes(payload: Dict[str, Any], *, compress: bool = False) -> bytes:
    raw = json.dumps(payload, indent=2, default=_json_default).encode("utf-8")
    if not compress:
        return raw
    return gzip.compress(raw)


def _decode_json_bytes(raw: bytes, *, compressed: bool = False) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        payload = gzip.decompress(raw) if compressed else raw
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


class _ArtifactBackend:
    backend_name = "unknown"
    is_durable = False

    def version_key(self, version_id: str) -> str:
        return _safe_slug(version_id)

    def entry_pathname(self, version_id: str) -> str:
        return f"{self.entries_prefix}/{self.version_key(version_id)}.json"

    def snapshot_pathname(self, version_id: str) -> str:
        return f"{self.versions_prefix}/{self.version_key(version_id)}/snapshot.json.gz"

    def summary_pathname(self, version_id: str) -> str:
        return f"{self.versions_prefix}/{self.version_key(version_id)}/summary.json.gz"

    def get_entry_exact(self, version_id: str) -> Optional[Dict[str, Any]]:
        return self.read_json(self.entry_pathname(version_id))

    def write_entry(self, version_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entry)
        payload["entryPathname"] = self.entry_pathname(version_id)
        metadata = self.write_bytes(
            self.entry_pathname(version_id),
            _json_bytes(payload),
            content_type="application/json; charset=utf-8",
            allow_overwrite=True,
        )
        merged = dict(payload)
        merged.update(
            {
                "entryPathname": metadata.get("pathname") or self.entry_pathname(version_id),
                "entryUrl": metadata.get("url"),
            }
        )
        return merged

    def list_entries(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for pathname in self.list_pathnames(self.entries_prefix.rstrip("/") + "/"):
            payload = self.read_json(pathname)
            if isinstance(payload, dict):
                entries.append(payload)
        return entries

    def load_snapshot_payload(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pathname = entry.get("snapshotPathname")
        if not pathname and entry.get("snapshotPath"):
            pathname = self.pathname_from_legacy_path(entry["snapshotPath"])
        if not pathname:
            return None
        return self.read_json(pathname)

    def load_summary_payload(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pathname = entry.get("summaryPathname")
        if not pathname and entry.get("summaryPath"):
            pathname = self.pathname_from_legacy_path(entry["summaryPath"])
        if not pathname:
            return None
        return self.read_json(pathname)

    def describe(self) -> Dict[str, Any]:
        return {
            "backend": self.backend_name,
            "durable": self.is_durable,
        }

    def pathname_from_legacy_path(self, value: str) -> Optional[str]:
        return value

    def read_json(self, pathname: str) -> Optional[Dict[str, Any]]:
        raw = self.read_bytes(pathname)
        return _decode_json_bytes(raw, compressed=pathname.endswith(".gz")) if raw is not None else None

    def read_bytes(self, pathname: str) -> Optional[bytes]:
        raise NotImplementedError

    def write_bytes(
        self,
        pathname: str,
        payload: bytes,
        *,
        content_type: str,
        allow_overwrite: bool,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def list_pathnames(self, prefix: str) -> List[str]:
        raise NotImplementedError


class _FileArtifactBackend(_ArtifactBackend):
    backend_name = "filesystem"
    is_durable = False

    def __init__(self, base_path: Optional[str] = None):
        if base_path is None:
            server_dir = Path(__file__).resolve().parent
            if os.getenv("VERCEL"):
                base_path = os.path.join("/tmp", "hackporto", "indexed-artifacts")
            else:
                base_path = str(server_dir / "indexed-artifacts")

        self.base_path = Path(base_path)
        self.entries_prefix = "entries"
        self.versions_prefix = "versions"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _full_path(self, pathname: str) -> Path:
        return self.base_path / pathname

    def pathname_from_legacy_path(self, value: str) -> Optional[str]:
        try:
            path = Path(value)
            return str(path.relative_to(self.base_path)).replace(os.sep, "/")
        except Exception:
            return None

    def read_bytes(self, pathname: str) -> Optional[bytes]:
        file_path = self._full_path(pathname)
        if not file_path.is_file():
            return None
        try:
            return file_path.read_bytes()
        except Exception:
            return None

    def write_bytes(
        self,
        pathname: str,
        payload: bytes,
        *,
        content_type: str,
        allow_overwrite: bool,
    ) -> Dict[str, Any]:
        del content_type
        target = self._full_path(pathname)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not allow_overwrite:
            raise FileExistsError(f"Artifact already exists at {pathname}")
        temp_path = target.with_suffix(target.suffix + ".tmp")
        temp_path.write_bytes(payload)
        temp_path.replace(target)
        return {
            "pathname": pathname,
            "path": str(target),
        }

    def list_pathnames(self, prefix: str) -> List[str]:
        prefix_path = self._full_path(prefix)
        if prefix.endswith("/"):
            root = prefix_path
        else:
            root = prefix_path.parent
        if not root.exists():
            return []
        pathnames: List[str] = []
        for file_path in root.rglob("*.json"):
            if file_path.is_file():
                rel_path = str(file_path.relative_to(self.base_path)).replace(os.sep, "/")
                if rel_path.startswith(prefix):
                    pathnames.append(rel_path)
        return sorted(pathnames)

    def describe(self) -> Dict[str, Any]:
        payload = super().describe()
        payload["basePath"] = str(self.base_path)
        return payload


class _BlobArtifactBackend(_ArtifactBackend):
    backend_name = "vercel-blob"
    is_durable = True

    def __init__(self, token: Optional[str] = None, prefix: Optional[str] = None):
        self.token = (token or os.environ.get("BLOB_READ_WRITE_TOKEN") or "").strip()
        if not self.token:
            raise ValueError("BLOB_READ_WRITE_TOKEN is required for the blob artifact backend")

        token_parts = self.token.split("_")
        self.store_id = token_parts[3] if len(token_parts) >= 4 else ""
        if not self.store_id:
            raise ValueError("BLOB_READ_WRITE_TOKEN does not contain a Vercel Blob store id")

        base_prefix = (prefix or os.environ.get("PREINDEX_BLOB_PREFIX") or "indexed-artifacts").strip().strip("/")
        if not base_prefix:
            base_prefix = "indexed-artifacts"
        self.entries_prefix = f"{base_prefix}/entries"
        self.versions_prefix = f"{base_prefix}/versions"
        self.access = (os.environ.get("PREINDEX_BLOB_ACCESS") or "private").strip().lower() or "private"
        if self.access not in {"private", "public"}:
            self.access = "private"

    def _blob_url(self, pathname: str) -> str:
        safe_path = quote(pathname, safe="/-._~")
        return f"https://{self.store_id}.{self.access}.blob.vercel-storage.com/{safe_path}"

    def _api_request(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
    ) -> Optional[Dict[str, Any]]:
        request_headers = {
            "authorization": f"Bearer {self.token}",
            "x-api-version": _BLOB_API_VERSION,
        }
        if headers:
            request_headers.update(headers)
        request = Request(
            f"{_BLOB_API_URL}{path}",
            data=body,
            method=method,
            headers=request_headers,
        )
        try:
            with urlopen(request) as response:
                payload = response.read()
        except HTTPError as exc:
            if exc.code == 404:
                return None
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Blob API {method} {path} failed: {exc.code} {details}") from exc
        except URLError as exc:
            raise RuntimeError(f"Blob API request failed: {exc}") from exc

        if not payload:
            return {}
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, dict):
            return data
        raise RuntimeError("Unexpected non-object response from Vercel Blob API")

    def read_bytes(self, pathname: str) -> Optional[bytes]:
        request = Request(
            self._blob_url(pathname),
            headers={"authorization": f"Bearer {self.token}"},
            method="GET",
        )
        try:
            with urlopen(request) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code == 404:
                return None
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Blob fetch failed for {pathname}: {exc.code} {details}") from exc
        except URLError as exc:
            raise RuntimeError(f"Blob fetch failed for {pathname}: {exc}") from exc

    def write_bytes(
        self,
        pathname: str,
        payload: bytes,
        *,
        content_type: str,
        allow_overwrite: bool,
    ) -> Dict[str, Any]:
        params = urlencode({"pathname": pathname})
        result = self._api_request(
            f"/?{params}",
            method="PUT",
            body=payload,
            headers={
                "x-vercel-blob-access": self.access,
                "x-add-random-suffix": "0",
                "x-allow-overwrite": "1" if allow_overwrite else "0",
                "x-content-type": content_type,
                "x-content-length": str(len(payload)),
            },
        )
        if not result:
            raise RuntimeError(f"Empty Blob API response while writing {pathname}")
        return result

    def list_pathnames(self, prefix: str) -> List[str]:
        pathnames: List[str] = []
        cursor: Optional[str] = None
        while True:
            query = {"limit": "1000", "prefix": prefix}
            if cursor:
                query["cursor"] = cursor
            result = self._api_request(f"?{urlencode(query)}", method="GET") or {}
            blobs = result.get("blobs") or []
            for blob in blobs:
                pathname = blob.get("pathname")
                if isinstance(pathname, str) and pathname.startswith(prefix):
                    pathnames.append(pathname)
            if not result.get("hasMore"):
                break
            cursor = result.get("cursor")
            if not cursor:
                break
        return sorted(pathnames)

    def describe(self) -> Dict[str, Any]:
        payload = super().describe()
        payload["blobPrefix"] = self.entries_prefix.rsplit("/", 1)[0]
        return payload


class IndexedArtifactStore:
    """Persist prebuilt indexed artifacts and load them as MemoryTrees."""

    MAX_TREE_CACHE_SIZE = 6

    def __init__(
        self,
        memory_tree_class,
        base_path: Optional[str] = None,
        backend: Optional[str] = None,
        blob_prefix: Optional[str] = None,
        blob_token: Optional[str] = None,
    ):
        backend_choice = (backend or os.environ.get("PREINDEX_STORAGE_BACKEND") or "auto").strip().lower()
        if backend_choice == "auto":
            if os.environ.get("VERCEL") and (blob_token or os.environ.get("BLOB_READ_WRITE_TOKEN")):
                backend_choice = "blob"
            else:
                backend_choice = "filesystem"

        if backend_choice in {"blob", "vercel-blob"}:
            self.backend = _BlobArtifactBackend(token=blob_token, prefix=blob_prefix)
        elif backend_choice in {"file", "filesystem", "local"}:
            self.backend = _FileArtifactBackend(base_path=base_path)
        else:
            raise ValueError(f"Unknown indexed artifact backend: {backend_choice}")

        self._MemoryTree = memory_tree_class
        self._lock = RLock()
        self._tree_cache: Dict[str, Any] = {}
        self._cache_order: List[str] = []

    def build_version_id(self, repo_owner: str, repo_name: str, commit_sha: str, file_path: str) -> str:
        source = f"{repo_owner}/{repo_name}:{file_path}"
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]
        return f"{commit_sha}:{digest}"

    def upsert_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        version_id = (entry.get("versionId") or "").strip()
        if not version_id:
            raise ValueError("versionId is required for indexed artifacts")

        with self._lock:
            current = self.backend.get_entry_exact(version_id) or {}
            merged = {**current, **entry}
            merged.setdefault("createdAt", current.get("createdAt") or _utc_now_iso())
            merged["updatedAt"] = _utc_now_iso()
            merged["storageBackend"] = self.backend.backend_name
            merged["storageDurable"] = self.backend.is_durable
            return self.backend.write_entry(version_id, merged)

    def mark_pending(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(entry)
        payload["state"] = "pending"
        payload["error"] = None
        payload.setdefault("queuedAt", _utc_now_iso())
        return self.upsert_entry(payload)

    def mark_failed(self, entry: Dict[str, Any], error_message: str) -> Dict[str, Any]:
        payload = dict(entry)
        payload["state"] = "failed"
        payload["error"] = (error_message or "").strip() or "Unknown preindex failure"
        payload["failedAt"] = _utc_now_iso()
        return self.upsert_entry(payload)

    def store_ready_artifact(
        self,
        entry: Dict[str, Any],
        components: List[Dict[str, Any]],
        summary_by_id: Dict[str, Any],
    ) -> Dict[str, Any]:
        version_id = entry["versionId"]
        model_name = (entry.get("modelName") or "IndexedModel").strip() or "IndexedModel"

        snapshot_payload = {
            "versionId": version_id,
            "modelName": model_name,
            "models": {
                model_name: components,
            },
        }
        snapshot_meta = self.backend.write_bytes(
            self.backend.snapshot_pathname(version_id),
            _json_bytes(snapshot_payload, compress=True),
            content_type="application/json",
            allow_overwrite=True,
        )
        summary_meta = self.backend.write_bytes(
            self.backend.summary_pathname(version_id),
            _json_bytes(summary_by_id, compress=True),
            content_type="application/json",
            allow_overwrite=True,
        )

        payload = dict(entry)
        payload.update(
            {
                "state": "ready",
                "error": None,
                "indexedAt": _utc_now_iso(),
                "componentCount": len(components),
                "summaryCount": len(summary_by_id),
                "snapshotPathname": snapshot_meta.get("pathname"),
                "snapshotUrl": snapshot_meta.get("url"),
                "summaryPathname": summary_meta.get("pathname"),
                "summaryUrl": summary_meta.get("url"),
            }
        )
        payload.pop("queryStorePath", None)
        stored = self.upsert_entry(payload)

        with self._lock:
            self._tree_cache.pop(version_id, None)
            self._cache_order = [cached for cached in self._cache_order if cached != version_id]

        return stored

    def list_entries(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        entries = self.backend.list_entries()
        entries.sort(
            key=lambda item: (
                item.get("timestamp")
                or item.get("indexedAt")
                or item.get("updatedAt")
                or item.get("createdAt")
                or ""
            ),
            reverse=True,
        )
        if limit is not None:
            entries = entries[: max(0, limit)]
        return [dict(entry) for entry in entries]

    def get_entry(self, version: Optional[str]) -> Optional[Dict[str, Any]]:
        if not version:
            return None

        entry = self.backend.get_entry_exact(version)
        if entry:
            return dict(entry)

        all_entries = self.list_entries(limit=None)
        commit_matches = [item for item in all_entries if item.get("commitSha") == version]
        if len(commit_matches) == 1:
            return dict(commit_matches[0])

        short_matches = [
            item
            for item in all_entries
            if (item.get("commitSha") or "").startswith(version) or (item.get("shortId") == version)
        ]
        if len(short_matches) == 1:
            return dict(short_matches[0])

        return None

    def list_versions(self, limit: int = 50, ready_only: bool = True) -> List[Dict[str, Any]]:
        entries = self.list_entries(limit=None)
        versions = []
        for entry in entries:
            if ready_only and entry.get("state") != "ready":
                continue
            versions.append(
                {
                    "versionId": entry.get("versionId"),
                    "shortId": entry.get("shortId") or (entry.get("commitSha") or "")[:7],
                    "message": entry.get("message")
                    or f"Indexed {entry.get('filePath') or entry.get('modelName') or 'IFC file'}",
                    "timestamp": entry.get("timestamp") or entry.get("indexedAt"),
                    "author": entry.get("author") or "Preindex",
                    "filePath": entry.get("filePath"),
                    "modelName": entry.get("modelName"),
                    "repoOwner": entry.get("repoOwner"),
                    "repoName": entry.get("repoName"),
                    "ref": entry.get("ref"),
                    "state": entry.get("state"),
                }
            )
            if len(versions) >= max(1, limit):
                break
        return versions

    def get_latest_version_id(self) -> Optional[str]:
        versions = self.list_versions(limit=1, ready_only=True)
        if not versions:
            return None
        return versions[0].get("versionId")

    def load_memory_tree(self, version: str):
        entry = self.get_entry(version)
        if not entry or entry.get("state") != "ready":
            return None

        version_id = entry["versionId"]
        with self._lock:
            cached = self._tree_cache.get(version_id)
            if cached is not None:
                if version_id in self._cache_order:
                    self._cache_order.remove(version_id)
                self._cache_order.append(version_id)
                return cached

        snapshot_payload = self.backend.load_snapshot_payload(entry)
        if snapshot_payload is None:
            return None

        tree = self._MemoryTree()
        tree.refresh_from_snapshot_payload(snapshot_payload)

        with self._lock:
            if len(self._tree_cache) >= self.MAX_TREE_CACHE_SIZE and self._cache_order:
                oldest = self._cache_order.pop(0)
                self._tree_cache.pop(oldest, None)
            self._tree_cache[version_id] = tree
            if version_id in self._cache_order:
                self._cache_order.remove(version_id)
            self._cache_order.append(version_id)

        return tree

    def read_summary(self, version: str) -> Optional[Dict[str, Any]]:
        entry = self.get_entry(version)
        if not entry or entry.get("state") != "ready":
            return None
        return self.backend.load_summary_payload(entry)

    def get_summary(self) -> Dict[str, Any]:
        entries = self.list_entries(limit=None)
        counts = {"ready": 0, "pending": 0, "failed": 0, "missing": 0}
        for entry in entries:
            state = entry.get("state") or "missing"
            if state not in counts:
                counts[state] = 0
            counts[state] += 1

        return {
            **self.backend.describe(),
            "counts": counts,
            "latestReadyVersion": self.get_latest_version_id(),
            "totalEntries": len(entries),
        }
