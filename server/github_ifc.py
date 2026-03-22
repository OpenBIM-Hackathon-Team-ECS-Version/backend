"""Helpers for fetching IFC files from GitHub refs or URLs."""

from dataclasses import dataclass
import re
from typing import Optional
from urllib import error, parse, request


class GitHubFetchError(RuntimeError):
    """Raised when GitHub content could not be fetched."""

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


class GitHubFileNotFoundError(GitHubFetchError):
    """Raised when the requested file is missing at a ref."""

    def __init__(self, message):
        super().__init__(message, status_code=404)


@dataclass(frozen=True)
class GitHubModelRef:
    """Minimal information needed to fetch a GitHub-hosted IFC revision."""

    repo_owner: str
    repo_name: str
    commit_sha: str
    file_path: str
    github_url: Optional[str] = None


RAW_GITHUB_RE = re.compile(
    r"^https://raw\.githubusercontent\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<sha>[^/]+)/(?P<path>.+)$"
)
GITHUB_BLOB_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<sha>[^/]+)/(?P<path>.+)$"
)


def parse_github_model_url(url):
    """Parse a GitHub model URL into a model ref payload."""

    trimmed = (url or "").strip()
    if not trimmed:
        raise ValueError("GitHub model URL is empty.")

    for pattern in (RAW_GITHUB_RE, GITHUB_BLOB_RE):
        match = pattern.match(trimmed)
        if match:
            groups = match.groupdict()
            return GitHubModelRef(
                repo_owner=groups["owner"],
                repo_name=groups["repo"],
                commit_sha=groups["sha"],
                file_path=parse.unquote(groups["path"]),
                github_url=trimmed,
            )

    raise ValueError(
        "Unsupported GitHub model URL. Use a raw.githubusercontent.com URL or a github.com/.../blob/... URL."
    )


def _build_raw_url(model_ref: GitHubModelRef):
    if model_ref.github_url and "raw.githubusercontent.com" in model_ref.github_url:
        return model_ref.github_url
    quoted_path = parse.quote(model_ref.file_path.lstrip("/"), safe="/")
    return (
        f"https://raw.githubusercontent.com/"
        f"{model_ref.repo_owner}/{model_ref.repo_name}/{model_ref.commit_sha}/{quoted_path}"
    )


def _build_api_url(model_ref: GitHubModelRef):
    quoted_path = parse.quote(model_ref.file_path.lstrip("/"), safe="/")
    quoted_ref = parse.quote(model_ref.commit_sha, safe="")
    return (
        f"https://api.github.com/repos/{model_ref.repo_owner}/{model_ref.repo_name}/contents/"
        f"{quoted_path}?ref={quoted_ref}"
    )


def fetch_ifc_bytes(model_ref: GitHubModelRef, github_token=None):
    """Fetch IFC bytes from GitHub using either raw or API endpoints."""

    raw_headers = {
        "User-Agent": "HackPorto-IFC-Diff",
        "Accept": "application/octet-stream",
    }
    raw_url = _build_raw_url(model_ref)

    # Prefer raw file delivery first to avoid burning REST API quota on public repos.
    try:
        req = request.Request(raw_url, headers=raw_headers)
        with request.urlopen(req) as response:
            return response.read()
    except error.HTTPError as exc:
        if not github_token or not github_token.strip():
            if exc.code == 404:
                raise GitHubFileNotFoundError(
                    f"IFC file not found for {model_ref.repo_owner}/{model_ref.repo_name} "
                    f"at {model_ref.commit_sha[:7]}:{model_ref.file_path}"
                ) from exc

            message = exc.read().decode("utf-8", errors="ignore").strip()
            raise GitHubFetchError(
                message
                or (
                    f"GitHub request failed with HTTP {exc.code} for "
                    f"{model_ref.repo_owner}/{model_ref.repo_name}:{model_ref.file_path}"
                ),
                status_code=exc.code,
            ) from exc
    except error.URLError as exc:
        if not github_token or not github_token.strip():
            raise GitHubFetchError(f"Unable to reach GitHub: {exc.reason}") from exc

    headers = {
        "User-Agent": "HackPorto-IFC-Diff",
        "Accept": "application/vnd.github.raw",
        "Authorization": f"Bearer {github_token.strip()}",
    }
    url = _build_api_url(model_ref)

    try:
        req = request.Request(url, headers=headers)
        with request.urlopen(req) as response:
            return response.read()
    except error.HTTPError as exc:
        if exc.code == 404:
            raise GitHubFileNotFoundError(
                f"IFC file not found for {model_ref.repo_owner}/{model_ref.repo_name} "
                f"at {model_ref.commit_sha[:7]}:{model_ref.file_path}"
            ) from exc

        message = exc.read().decode("utf-8", errors="ignore").strip()
        raise GitHubFetchError(
            message
            or (
                f"GitHub request failed with HTTP {exc.code} for "
                f"{model_ref.repo_owner}/{model_ref.repo_name}:{model_ref.file_path}"
            ),
            status_code=exc.code,
        ) from exc
    except error.URLError as exc:
        raise GitHubFetchError(f"Unable to reach GitHub: {exc.reason}") from exc
