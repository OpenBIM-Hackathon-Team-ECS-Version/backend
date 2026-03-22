"""Helpers for proxying GitHub repository data to the frontend."""

from datetime import datetime, timezone
import json
from urllib import error, parse, request

from github_ifc import GitHubModelRef, fetch_ifc_bytes


class GitHubProxyError(RuntimeError):
    """Raised when a GitHub proxy request fails."""

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def _parse_iso_datetime(value):
    if not value:
        return datetime.now(timezone.utc)

    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _format_relative_time(iso_date):
    dt = _parse_iso_datetime(iso_date)
    diff_minutes = round((dt - datetime.now(timezone.utc)).total_seconds() / 60)

    if abs(diff_minutes) < 60:
        unit_value = diff_minutes
        unit = "minute"
    else:
        diff_hours = round(diff_minutes / 60)
        if abs(diff_hours) < 24:
            unit_value = diff_hours
            unit = "hour"
        else:
            diff_days = round(diff_hours / 24)
            if abs(diff_days) < 30:
                unit_value = diff_days
                unit = "day"
            else:
                diff_months = round(diff_days / 30)
                if abs(diff_months) < 12:
                    unit_value = diff_months
                    unit = "month"
                else:
                    unit_value = round(diff_months / 12)
                    unit = "year"

    if unit_value == 0:
        return "now"

    suffix = "ago" if unit_value < 0 else "from now"
    absolute = abs(unit_value)
    label = unit if absolute == 1 else f"{unit}s"
    return f"{absolute} {label} {suffix}"


def _request_json(url, github_token=None):
    headers = {
        "User-Agent": "HackPorto-GitHub-Proxy",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token.strip()}"

    try:
        req = request.Request(url, headers=headers)
        with request.urlopen(req) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload)
    except error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore").strip()
        raise GitHubProxyError(
            message or f"GitHub request failed with HTTP {exc.code}",
            status_code=exc.code,
        ) from exc
    except error.URLError as exc:
        raise GitHubProxyError(f"Unable to reach GitHub: {exc.reason}") from exc


def _to_commit_payload(commit, branch_name):
    commit_meta = commit.get("commit") or {}
    author_meta = commit_meta.get("author") or {}
    committer_meta = commit_meta.get("committer") or {}
    authored_at = (
        author_meta.get("date")
        or committer_meta.get("date")
        or datetime.now(timezone.utc).isoformat()
    )

    author = commit.get("author") or {}
    parents = commit.get("parents") or []

    return {
        "sha": commit.get("sha", ""),
        "shortSha": (commit.get("sha") or "")[:7],
        "message": commit_meta.get("message", ""),
        "authoredAt": authored_at,
        "relativeTime": _format_relative_time(authored_at),
        "authorName": (
            author_meta.get("name")
            or author.get("login")
            or committer_meta.get("name")
            or "Unknown author"
        ),
        "authorAvatarUrl": author.get("avatar_url"),
        "parentShas": [
            parent.get("sha", "")
            for parent in parents
            if isinstance(parent, dict) and parent.get("sha")
        ],
        "branchNames": [branch_name],
    }


def list_branches(repo_owner, repo_name, github_token=None, per_page=20):
    query = parse.urlencode({"per_page": max(1, min(int(per_page), 100))})
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/branches?{query}"
    data = _request_json(url, github_token=github_token)

    return [
        {
            "name": (branch.get("name") or "").strip(),
            "sha": ((branch.get("commit") or {}).get("sha") or "").strip(),
            "protected": bool(branch.get("protected")),
        }
        for branch in data
        if isinstance(branch, dict)
    ]


def list_commits(repo_owner, repo_name, ref_name, github_token=None, per_page=35, path=None):
    query = {
        "sha": ref_name,
        "per_page": max(1, min(int(per_page), 100)),
    }
    if path:
        query["path"] = path

    url = (
        f"https://api.github.com/repos/{repo_owner}/{repo_name}/commits?"
        f"{parse.urlencode(query)}"
    )
    data = _request_json(url, github_token=github_token)
    return [_to_commit_payload(commit, ref_name) for commit in data if isinstance(commit, dict)]


def get_commit_details(repo_owner, repo_name, ref_name, github_token=None):
    url = (
        f"https://api.github.com/repos/{repo_owner}/{repo_name}/commits/"
        f"{parse.quote(ref_name, safe='')}"
    )
    commit = _request_json(url, github_token=github_token)
    if not isinstance(commit, dict):
        raise GitHubProxyError("Invalid commit response from GitHub")

    payload = _to_commit_payload(commit, ref_name)
    tree = ((commit.get("commit") or {}).get("tree") or {})
    payload["treeSha"] = (tree.get("sha") or "").strip()
    return payload


def get_repo_tree(repo_owner, repo_name, tree_sha, github_token=None):
    query = parse.urlencode({"recursive": "1"})
    url = (
        f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/trees/"
        f"{parse.quote(tree_sha, safe='')}?{query}"
    )
    data = _request_json(url, github_token=github_token)
    tree_entries = data.get("tree") or []

    result = []
    for entry in tree_entries:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type not in {"blob", "tree"}:
            continue
        path_value = entry.get("path")
        sha_value = entry.get("sha")
        if not isinstance(path_value, str) or not isinstance(sha_value, str):
            continue
        item = {
            "path": path_value,
            "sha": sha_value,
            "type": entry_type,
        }
        if isinstance(entry.get("size"), int):
            item["size"] = entry["size"]
        result.append(item)

    return result


def fetch_file_bytes(repo_owner, repo_name, ref_name, file_path, github_token=None):
    model_ref = GitHubModelRef(
        repo_owner=repo_owner,
        repo_name=repo_name,
        commit_sha=ref_name,
        file_path=file_path,
        github_url=None,
    )
    return fetch_ifc_bytes(model_ref, github_token=github_token)
