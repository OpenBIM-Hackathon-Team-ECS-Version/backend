"""Filesystem-backed indexed artifact store for demo preindexing."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value):
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return str(value)


def _safe_slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value).strip("-") or "artifact"


class IndexedArtifactStore:
    """Persist prebuilt indexed artifacts and load them as MemoryTrees."""

    MAX_TREE_CACHE_SIZE = 6

    def __init__(self, memory_tree_class, base_path: Optional[str] = None):
        if base_path is None:
            server_dir = Path(__file__).resolve().parent
            if os.getenv("VERCEL"):
                base_path = os.path.join("/tmp", "hackporto", "indexed-artifacts")
            else:
                base_path = str(server_dir / "indexed-artifacts")

        self.base_path = Path(base_path)
        self.entries_path = self.base_path / "entries"
        self.versions_path = self.base_path / "versions"
        self.catalog_path = self.base_path / "catalog.json"
        self._MemoryTree = memory_tree_class
        self._lock = RLock()
        self._tree_cache: Dict[str, Any] = {}
        self._cache_order: List[str] = []

        self.versions_path.mkdir(parents=True, exist_ok=True)
        self._ensure_catalog()

    def _ensure_catalog(self):
        if not self.catalog_path.exists():
            self._save_catalog({"entries": {}})

    def _load_catalog(self) -> Dict[str, Any]:
        try:
            with self.catalog_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
                if isinstance(payload, dict) and isinstance(payload.get("entries"), dict):
                    return payload
        except Exception:
            pass
        return {"entries": {}}

    def _save_catalog(self, payload: Dict[str, Any]):
        self.base_path.mkdir(parents=True, exist_ok=True)
        temp_path = self.catalog_path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=_json_default)
        temp_path.replace(self.catalog_path)

    def build_version_id(self, repo_owner: str, repo_name: str, commit_sha: str, file_path: str) -> str:
        source = f"{repo_owner}/{repo_name}:{file_path}"
        digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]
        return f"{commit_sha}:{digest}"

    def _version_dir(self, version_id: str) -> Path:
        return self.versions_path / _safe_slug(version_id)

    def upsert_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        version_id = (entry.get("versionId") or "").strip()
        if not version_id:
            raise ValueError("versionId is required for indexed artifacts")

        with self._lock:
            payload = self._load_catalog()
            current = payload["entries"].get(version_id, {})
            merged = {**current, **entry}
            merged.setdefault("createdAt", current.get("createdAt") or _utc_now_iso())
            merged["updatedAt"] = _utc_now_iso()
            payload["entries"][version_id] = merged
            self._save_catalog(payload)
            return dict(merged)

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
        version_dir = self._version_dir(version_id)
        snapshot_path = version_dir / "snapshot.json.gz"
        summary_path = version_dir / "summary.json.gz"

        if version_dir.exists():
            shutil.rmtree(version_dir)
        version_dir.mkdir(parents=True, exist_ok=True)

        snapshot_payload = {
            "versionId": version_id,
            "modelName": model_name,
            "models": {
                model_name: components,
            },
        }
        with gzip.open(snapshot_path, "wt", encoding="utf-8") as handle:
            json.dump(snapshot_payload, handle, indent=2, default=_json_default)
        with gzip.open(summary_path, "wt", encoding="utf-8") as handle:
            json.dump(summary_by_id, handle, indent=2, default=_json_default)

        payload = dict(entry)
        payload.update(
            {
                "state": "ready",
                "error": None,
                "indexedAt": _utc_now_iso(),
                "componentCount": len(components),
                "summaryCount": len(summary_by_id),
                "snapshotPath": str(snapshot_path),
                "summaryPath": str(summary_path),
                "artifactPath": str(version_dir),
            }
        )
        payload.pop("queryStorePath", None)
        stored = self.upsert_entry(payload)

        with self._lock:
            self._tree_cache.pop(version_id, None)
            self._cache_order = [cached for cached in self._cache_order if cached != version_id]

        return stored

    def list_entries(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        payload = self._load_catalog()
        entries = list(payload.get("entries", {}).values())
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

        payload = self._load_catalog()
        entries = payload.get("entries", {})
        if version in entries:
            return dict(entries[version])

        all_entries = list(entries.values())
        commit_matches = [entry for entry in all_entries if entry.get("commitSha") == version]
        if len(commit_matches) == 1:
            return dict(commit_matches[0])

        short_matches = [
            entry
            for entry in all_entries
            if (entry.get("commitSha") or "").startswith(version) or (entry.get("shortId") == version)
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

        tree = self._MemoryTree()
        snapshot_path = entry.get("snapshotPath")
        if snapshot_path and os.path.isfile(snapshot_path):
            tree.refresh_from_snapshot(snapshot_path)
        else:
            query_store_path = entry.get("queryStorePath")
            if not query_store_path or not os.path.isdir(query_store_path):
                return None
            tree.refresh_from_store(query_store_path)

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

        summary_path = entry.get("summaryPath")
        if not summary_path or not os.path.isfile(summary_path):
            return None

        opener = gzip.open if summary_path.endswith(".gz") else open
        try:
            with opener(summary_path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
                if isinstance(payload, dict):
                    return payload
        except Exception:
            return None
        return None

    def get_summary(self) -> Dict[str, Any]:
        entries = self.list_entries(limit=None)
        counts = {"ready": 0, "pending": 0, "failed": 0, "missing": 0}
        for entry in entries:
            state = entry.get("state") or "missing"
            if state not in counts:
                counts[state] = 0
            counts[state] += 1

        return {
            "basePath": str(self.base_path),
            "counts": counts,
            "latestReadyVersion": self.get_latest_version_id(),
            "totalEntries": len(entries),
        }
