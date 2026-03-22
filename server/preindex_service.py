"""Best-effort async IFC preindexing for demo-tracked GitHub files."""

from __future__ import annotations

import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Tuple

from ifc4ingestor import IFC2JSONSimple
from ifc_diff_service import summarize_ifc_bytes
from github_proxy import (
    fetch_file_bytes,
    get_commit_details,
    get_repo_tree,
    list_commits,
)


class DemoPreindexService:
    """Manage tracked-file discovery and background preindex jobs."""

    def __init__(
        self,
        artifact_store,
        github_token_resolver: Optional[Callable[[], Optional[str]]] = None,
        manifest_path: Optional[str] = None,
        max_workers: int = 1,
    ):
        server_dir = Path(__file__).resolve().parent
        self.artifact_store = artifact_store
        self.github_token_resolver = github_token_resolver or (lambda: None)
        self.manifest_path = Path(
            manifest_path
            or os.environ.get("PREINDEX_MANIFEST_PATH")
            or (server_dir / "preindex_manifest.json")
        )
        self.max_workers = max(1, int(max_workers))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="hackporto-preindex",
        )
        self._lock = RLock()
        self._running_tasks: Dict[str, str] = {}
        self._tracked_cache: List[Dict[str, Any]] = []

    def _resolve_github_token(self) -> Optional[str]:
        token = self.github_token_resolver()
        return token.strip() if isinstance(token, str) and token.strip() else None

    def _load_manifest(self) -> Dict[str, Any]:
        raw_json = os.environ.get("PREINDEX_MANIFEST_JSON")
        if raw_json and raw_json.strip():
            try:
                payload = json.loads(raw_json)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                pass

        if self.manifest_path.is_file():
            try:
                with self.manifest_path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                    if isinstance(payload, dict):
                        return payload
            except Exception:
                pass

        return {"trackedFiles": [], "trackedSets": []}

    def is_enabled(self) -> bool:
        manifest = self._load_manifest()
        return bool(manifest.get("trackedFiles") or manifest.get("trackedSets"))

    def _build_explicit_item(self, item: Dict[str, Any], ref_name: str, file_path: str) -> Dict[str, Any]:
        return {
            "repoOwner": (item.get("repoOwner") or "").strip(),
            "repoName": (item.get("repoName") or "").strip(),
            "ref": ref_name.strip(),
            "filePath": file_path.strip().lstrip("/"),
        }

    def _discover_ifc_paths(self, repo_owner: str, repo_name: str, ref_name: str) -> List[str]:
        token = self._resolve_github_token()
        commit = get_commit_details(repo_owner, repo_name, ref_name, github_token=token)
        tree_sha = (commit.get("treeSha") or "").strip()
        if not tree_sha:
            return []

        tree_entries = get_repo_tree(repo_owner, repo_name, tree_sha, github_token=token)
        paths = []
        for entry in tree_entries:
            if entry.get("type") != "blob":
                continue
            path_value = (entry.get("path") or "").strip()
            if path_value.lower().endswith(".ifc"):
                paths.append(path_value)
        return sorted(paths)

    def get_tracked_files(self, force_reload: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            if self._tracked_cache and not force_reload:
                return [dict(item) for item in self._tracked_cache]

        manifest = self._load_manifest()
        tracked: List[Dict[str, Any]] = []

        for item in manifest.get("trackedFiles") or []:
            if not isinstance(item, dict):
                continue
            repo_owner = (item.get("repoOwner") or "").strip()
            repo_name = (item.get("repoName") or "").strip()
            ref_name = (item.get("ref") or "main").strip()
            file_path = (item.get("filePath") or "").strip()
            if repo_owner and repo_name and ref_name and file_path:
                tracked.append(self._build_explicit_item(item, ref_name, file_path))

        for item in manifest.get("trackedSets") or []:
            if not isinstance(item, dict):
                continue
            repo_owner = (item.get("repoOwner") or "").strip()
            repo_name = (item.get("repoName") or "").strip()
            refs = item.get("refs") or []
            if item.get("ref"):
                refs = list(refs) + [item.get("ref")]
            explicit_paths = [str(path).strip() for path in (item.get("filePaths") or []) if str(path).strip()]
            discover_ifc_files = bool(item.get("discoverIfcFiles"))

            if not (repo_owner and repo_name and refs):
                continue

            for ref_name in refs:
                ref_name = str(ref_name).strip()
                if not ref_name:
                    continue
                for file_path in explicit_paths:
                    tracked.append(self._build_explicit_item(item, ref_name, file_path))
                if discover_ifc_files:
                    try:
                        for file_path in self._discover_ifc_paths(repo_owner, repo_name, ref_name):
                            tracked.append(self._build_explicit_item(item, ref_name, file_path))
                    except Exception as exc:
                        print(f"[WARN] IFC discovery failed for {repo_owner}/{repo_name}@{ref_name}: {exc}")

        deduped: List[Dict[str, Any]] = []
        seen: set[Tuple[str, str, str, str]] = set()
        for item in tracked:
            key = (
                item.get("repoOwner", ""),
                item.get("repoName", ""),
                item.get("ref", ""),
                item.get("filePath", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        with self._lock:
            self._tracked_cache = [dict(item) for item in deduped]
        return deduped

    def _resolve_version_metadata(self, tracked_file: Dict[str, Any]) -> Dict[str, Any]:
        repo_owner = tracked_file["repoOwner"]
        repo_name = tracked_file["repoName"]
        ref_name = tracked_file["ref"]
        file_path = tracked_file["filePath"]
        token = self._resolve_github_token()

        commits = list_commits(
            repo_owner,
            repo_name,
            ref_name,
            github_token=token,
            per_page=1,
            path=file_path,
        )
        if commits:
            commit = commits[0]
        else:
            commit = get_commit_details(repo_owner, repo_name, ref_name, github_token=token)

        commit_sha = (commit.get("sha") or "").strip()
        if not commit_sha:
            raise ValueError(f"Could not resolve commit for {repo_owner}/{repo_name}@{ref_name}:{file_path}")

        model_name = Path(file_path).stem or "IndexedModel"
        version_id = self.artifact_store.build_version_id(repo_owner, repo_name, commit_sha, file_path)
        return {
            "versionId": version_id,
            "shortId": commit_sha[:7],
            "commitSha": commit_sha,
            "message": commit.get("message") or f"Indexed {file_path}",
            "timestamp": commit.get("authoredAt") or commit.get("timestamp"),
            "author": commit.get("authorName") or commit.get("author") or "Unknown author",
            "repoOwner": repo_owner,
            "repoName": repo_name,
            "ref": ref_name,
            "filePath": file_path,
            "modelName": model_name,
            "source": "github-preindex",
        }

    def _task_key(self, metadata: Dict[str, Any]) -> str:
        return (
            f"{metadata.get('repoOwner')}/{metadata.get('repoName')}"
            f"@{metadata.get('ref')}:{metadata.get('filePath')}"
        )

    def _components_from_ifc_bytes(self, ifc_bytes: bytes, model_name: str) -> List[Dict[str, Any]]:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as temp_file:
                temp_file.write(ifc_bytes)
                temp_path = temp_file.name
            converter = IFC2JSONSimple(temp_path, modelName=model_name)
            return converter.spf2Json()
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    def _run_index_task(self, metadata: Dict[str, Any]):
        task_key = self._task_key(metadata)
        try:
            token = self._resolve_github_token()
            ifc_bytes = fetch_file_bytes(
                metadata["repoOwner"],
                metadata["repoName"],
                metadata["commitSha"],
                metadata["filePath"],
                github_token=token,
            )
            components = self._components_from_ifc_bytes(ifc_bytes, metadata["modelName"])
            summary = summarize_ifc_bytes(ifc_bytes)
            self.artifact_store.store_ready_artifact(metadata, components, summary)
            print(
                f"[OK] Preindexed {metadata['repoOwner']}/{metadata['repoName']}"
                f" {metadata['filePath']} @ {metadata['commitSha'][:7]}"
            )
        except Exception as exc:
            self.artifact_store.mark_failed(metadata, str(exc))
            print(
                f"[WARN] Preindex failed for {metadata.get('repoOwner')}/{metadata.get('repoName')}"
                f" {metadata.get('filePath')} @ {metadata.get('ref')}: {exc}"
            )
        finally:
            with self._lock:
                self._running_tasks.pop(task_key, None)

    def enqueue_manifest(self, force: bool = False) -> Dict[str, Any]:
        tracked_files = self.get_tracked_files(force_reload=force)
        scheduled = 0
        skipped = 0
        failed = 0

        for tracked_file in tracked_files:
            try:
                metadata = self._resolve_version_metadata(tracked_file)
                existing = self.artifact_store.get_entry(metadata["versionId"])
                task_key = self._task_key(metadata)
                with self._lock:
                    version_already_running = metadata["versionId"] in self._running_tasks.values()
                    if task_key in self._running_tasks or version_already_running:
                        skipped += 1
                        continue
                    if (
                        existing
                        and existing.get("state") in {"ready", "pending"}
                        and not force
                    ):
                        skipped += 1
                        continue
                    self._running_tasks[task_key] = metadata["versionId"]

                self.artifact_store.mark_pending(metadata)
                self._executor.submit(self._run_index_task, metadata)
                scheduled += 1
            except Exception as exc:
                failed += 1
                print(f"[WARN] Failed to schedule preindex job: {exc}")

        return {
            "trackedCount": len(tracked_files),
            "scheduled": scheduled,
            "skipped": skipped,
            "failed": failed,
        }

    def load_memory_tree(self, version: Optional[str]):
        if not version:
            return None
        return self.artifact_store.load_memory_tree(version)

    def list_versions(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.artifact_store.list_versions(limit=limit, ready_only=True)

    def get_status(self, limit: int = 25) -> Dict[str, Any]:
        tracked_files = self.get_tracked_files(force_reload=False)
        with self._lock:
            running = dict(self._running_tasks)

        return {
            "enabled": self.is_enabled(),
            "manifestPath": str(self.manifest_path),
            "trackedCount": len(tracked_files),
            "runningCount": len(running),
            "runningTasks": running,
            "artifacts": self.artifact_store.get_summary(),
            "entries": self.artifact_store.list_entries(limit=limit),
        }
