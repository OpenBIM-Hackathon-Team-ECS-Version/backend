"""Git versioning support for the IFC file-based data store.

After each model upload the server creates a git commit containing the new
JSON component files and pushes to the remote.  Every commit SHA becomes a
stable "version ID" that clients can pass as ?version=<sha> to any data-query
endpoint so the server reconstructs and returns data as it existed at that
point in time.

Environment variables
---------------------
GITHUB_TOKEN   Personal-access token (PAT) used for authenticated HTTPS push.
               If not set the server falls back to whatever git credential
               helper / SSH key is already configured on the machine.
GIT_USER_NAME  Name recorded as the git commit author  (default: IFC Server).
GIT_USER_EMAIL E-mail recorded as the git commit author
               (default: ifc-server@localhost).
GIT_PUSH_REMOTE_URL
               Optional explicit remote URL to push to, e.g.
               https://github.com/OpenBIM-Hackathon-Team-ECS-Version/Sample-IFC-Files.git
GIT_PUSH_BRANCH
               Optional target branch for push. When using
               GIT_PUSH_REMOTE_URL and this is omitted, defaults to main.
"""

import io
import os
import re
import tarfile
import tempfile
import subprocess
from datetime import datetime
from typing import Dict, List, Optional

# Only allow safe 7-40 hex-char strings as version references to prevent
# command-injection via the ?version= query parameter.
_SHA_RE = re.compile(r'^[0-9a-fA-F]{7,40}$')


class GitVersionManager:
    """Manages git versioning for the file-based IFC data store."""

    MAX_CACHE_SIZE = 10  # max versioned MemoryTrees kept in RAM simultaneously

    def __init__(self,
                 repo_root: str,
                 memory_tree_class,
                 data_rel_path: str = 'server/dataStores/fileBased/data',
                 push_remote_url: Optional[str] = None,
                 push_branch: Optional[str] = None):
        """
        Args:
            repo_root          : Absolute path to the git repository root.
            memory_tree_class  : The MemoryTree class to instantiate when building
                                 versioned trees (injected to avoid import path issues).
            data_rel_path      : Path to the component data directory relative to
                                 repo_root.  Forward slashes required.
            push_remote_url    : Optional explicit remote URL to push commits to.
            push_branch        : Optional branch name to push to.
        """
        self.repo_root = str(repo_root)
        self._MemoryTree = memory_tree_class
        # Normalise separators so git commands work on Windows too.
        self.data_rel_path = data_rel_path.replace('\\', '/')
        self.push_remote_url = (push_remote_url or '').strip() or None
        self.push_branch = (push_branch or '').strip() or None
        self._version_cache: Dict = {}
        self._cache_order: List[str] = []
        self._git_available = self._check_git()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_git(self) -> bool:
        """Return True if git is on PATH and repo_root is a repository."""
        try:
            r = subprocess.run(
                ['git', 'rev-parse', '--git-dir'],
                cwd=self.repo_root, capture_output=True, text=True
            )
            return r.returncode == 0
        except FileNotFoundError:
            return False

    def _run_git(self, args: list, *, text: bool = True, check: bool = True):
        """Run *git args* in repo_root and return the CompletedProcess."""
        result = subprocess.run(
            ['git'] + [str(a) for a in args],
            cwd=self.repo_root, capture_output=True, text=text
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(str(a) for a in args)} failed:\n{result.stderr}"
            )
        return result

    def _configure_commit_identity(self):
        user = os.environ.get('GIT_USER_NAME', 'IFC Server')
        email = os.environ.get('GIT_USER_EMAIL', 'ifc-server@localhost')
        self._run_git(['config', 'user.name', user])
        self._run_git(['config', 'user.email', email])

    @staticmethod
    def _inject_token_if_https(url: str, token: Optional[str]) -> str:
        """Insert GitHub token into an HTTPS URL when a token is provided."""
        if token and url.startswith('https://'):
            return url.replace('https://', f'https://x-access-token:{token}@')
        return url

    def _push(self):
        """Push HEAD to configured remote, injecting GITHUB_TOKEN for HTTPS URLs."""
        token = os.environ.get('GITHUB_TOKEN')

        # If a specific remote URL is configured, push there directly.
        if self.push_remote_url:
            target_branch = self.push_branch or 'main'
            target_url = self._inject_token_if_https(self.push_remote_url, token)
            self._run_git(['push', target_url, f'HEAD:{target_branch}'])
            return

        # Otherwise use origin as before.
        if token:
            r = self._run_git(['remote', 'get-url', 'origin'])
            url = r.stdout.strip()
            if url.startswith('https://'):
                auth_url = self._inject_token_if_https(url, token)
                if self.push_branch:
                    self._run_git(['push', auth_url, f'HEAD:{self.push_branch}'])
                else:
                    self._run_git(['push', auth_url])
                return
        # Fall back to whatever credential helper / SSH key is configured.
        if self.push_branch:
            self._run_git(['push', 'origin', f'HEAD:{self.push_branch}'])
        else:
            self._run_git(['push'])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_valid_sha(self, sha: str) -> bool:
        """Return True if *sha* is a safe 7-40 hex-character git reference."""
        return bool(_SHA_RE.match(sha))

    def get_latest_sha(self) -> Optional[str]:
        """Return the current HEAD commit SHA, or None when unavailable."""
        if not self._git_available:
            return None
        try:
            return self._run_git(['rev-parse', 'HEAD']).stdout.strip()
        except Exception:
            return None

    def commit_and_push(self, model_name: str) -> Optional[str]:
        """Stage model data, create a git commit, push, and return the SHA.

        The returned SHA is the stable version ID clients can use in queries.
        Returns None when git is unavailable (the server continues without
        versioning in that case).
        """
        if not self._git_available:
            return None
        try:
            self._configure_commit_identity()
            model_path = f'{self.data_rel_path}/{model_name}'
            self._run_git(['add', model_path])

            # Bail out early when nothing was actually staged.
            diff = self._run_git(
                ['diff', '--cached', '--name-only', '--', model_path]
            )
            if not diff.stdout.strip():
                print(f'[GitVersionManager] No new files to commit for "{model_name}"')
                return self.get_latest_sha()

            ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            self._run_git(['commit', '-m', f'Add model: {model_name} [{ts}]'])
            sha = self.get_latest_sha()

            try:
                self._push()
                print(f'[GitVersionManager] Pushed version '
                      f'{sha[:8] if sha else "?"} for model "{model_name}"')
            except Exception as push_err:
                print(f'[GitVersionManager] Push failed '
                      f'(local commit succeeded): {push_err}')

            return sha
        except Exception as e:
            print(f'[GitVersionManager] commit_and_push failed: {e}')
            return None

    def commit_deletion_and_push(self, model_names: List[str]) -> Optional[str]:
        """Commit removal of one or more models from the git index and push."""
        if not self._git_available or not model_names:
            return None
        try:
            self._configure_commit_identity()
            staged_any = False
            for model_name in model_names:
                model_path = f'{self.data_rel_path}/{model_name}'
                r = self._run_git(
                    ['rm', '-r', '--cached', '--ignore-unmatch', model_path],
                    check=False
                )
                if r.stdout.strip():
                    staged_any = True

            if not staged_any:
                return self.get_latest_sha()

            names_str = ', '.join(model_names)
            ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            self._run_git(['commit', '-m', f'Remove model(s): {names_str} [{ts}]'])
            sha = self.get_latest_sha()

            try:
                self._push()
            except Exception as push_err:
                print(f'[GitVersionManager] Push failed '
                      f'(local commit succeeded): {push_err}')
            return sha
        except Exception as e:
            print(f'[GitVersionManager] commit_deletion_and_push failed: {e}')
            return None

    def list_versions(self, n: int = 50) -> List[Dict]:
        """Return up to *n* recent commits that touched the data directory.

        Each entry is ``{versionId, shortId, message, timestamp, author}``.
        """
        if not self._git_available:
            return []
        try:
            r = self._run_git([
                'log', f'-{max(1, n)}',
                '--format=%H|%h|%s|%aI|%an',
                '--', self.data_rel_path
            ])
            versions = []
            for line in r.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = line.split('|', 4)
                if len(parts) == 5:
                    versions.append({
                        'versionId': parts[0],
                        'shortId':   parts[1],
                        'message':   parts[2],
                        'timestamp': parts[3],
                        'author':    parts[4],
                    })
            return versions
        except Exception as e:
            print(f'[GitVersionManager] list_versions failed: {e}')
            return []

    def get_memory_tree_for_version(self, sha: str):
        """Return a MemoryTree populated with component data at git commit *sha*.

        Uses ``git archive`` to extract files efficiently without altering the
        working tree.  Results are cached (LRU, max MAX_CACHE_SIZE entries).
        The MemoryTree class is imported lazily; by the time this is called
        server.py will have already added the fileBased store directory to
        sys.path.
        """
        # --- cache lookup ---
        if sha in self._version_cache:
            if sha in self._cache_order:
                self._cache_order.remove(sha)
                self._cache_order.append(sha)
            return self._version_cache[sha]

        # Resolve short / partial SHA to the full 40-char SHA.
        try:
            full_sha = self._run_git(
                ['rev-parse', '--verify', sha]
            ).stdout.strip()
        except Exception:
            raise ValueError(f'Unknown or ambiguous version: {sha!r}')

        if full_sha in self._version_cache:
            self._version_cache[sha] = self._version_cache[full_sha]
            return self._version_cache[full_sha]

        # --- extract files at this version via git archive → temp dir ---
        with tempfile.TemporaryDirectory() as tmp_dir:
            arch = subprocess.run(
                ['git', 'archive', '--format=tar', full_sha,
                 '--', self.data_rel_path],
                cwd=self.repo_root, capture_output=True
            )
            if arch.returncode != 0:
                raise ValueError(
                    f'git archive failed for version {sha!r}: '
                    f'{arch.stderr.decode(errors="replace")}'
                )

            with tarfile.open(fileobj=io.BytesIO(arch.stdout)) as tar:
                tar.extractall(path=tmp_dir)

            data_dir = os.path.join(tmp_dir, *self.data_rel_path.split('/'))

            # Import lazily – server.py adds this directory to sys.path.
            tree = self._MemoryTree()
            if os.path.isdir(data_dir):
                tree.refresh_from_store(data_dir)
            else:
                print(f'[GitVersionManager] Data dir absent in archive '
                      f'for version {sha!r}')

        # --- LRU eviction ---
        if len(self._version_cache) >= self.MAX_CACHE_SIZE:
            oldest = self._cache_order.pop(0)
            self._version_cache.pop(oldest, None)

        self._version_cache[full_sha] = tree
        self._version_cache[sha] = tree
        self._cache_order.append(full_sha)
        return tree
