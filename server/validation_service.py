"""Client for the buildingSMART IFC Validation Service API.

Handles submitting IFC files for validation, polling for completion,
and fetching results (validation tasks and outcomes).

Replaces the standalone validation_serviced.py script with an importable
module that works with in-memory file bytes (e.g. fetched from GitHub).
"""

import json
import os
import requests
from dataclasses import dataclass, field
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # Optional in production when env vars are provided by the platform.
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


VALIDATION_API_URL = "https://dev.validate.buildingsmart.org/api/v1"


@dataclass
class ValidationResult:
    """Result of a validation request submission."""
    public_id: str
    status: str
    model_public_id: Optional[str] = None
    progress: int = 0
    raw: dict = field(default_factory=dict)


class ValidationServiceError(Exception):
    """Raised when the validation service returns an error."""

    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def _get_token():
    """Get the validation service token from environment or fallback."""
    return os.environ.get("VALIDATION_TOKEN", "c0e740b50591d1c82a30ebb1f0647256fc889af6")


def _headers(token=None):
    return {"Authorization": f"Token {token or _get_token()}"}


def submit_validation(file_bytes: bytes, file_name: str, token: str = None) -> ValidationResult:
    """Submit an IFC file for validation.

    Args:
        file_bytes: Raw IFC file content.
        file_name: Display name for the file.
        token: Optional API token override.

    Returns:
        ValidationResult with the request's public_id and initial status.
    """
    r = requests.post(
        f"{VALIDATION_API_URL}/validationrequest/",
        headers=_headers(token),
        files={"file": (file_name, file_bytes, "application/octet-stream")},
        data={"file_name": file_name},
        timeout=60,
    )

    if r.status_code != 201:
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        raise ValidationServiceError(
            f"Validation service returned {r.status_code}: {detail}",
            status_code=r.status_code,
        )

    data = r.json()
    return ValidationResult(
        public_id=data["public_id"],
        status=data.get("status", "PENDING"),
        model_public_id=data.get("model_public_id"),
        progress=data.get("progress", 0),
        raw=data,
    )


def get_validation_status(public_id: str, token: str = None) -> dict:
    """Poll the status of a validation request."""
    r = requests.get(
        f"{VALIDATION_API_URL}/validationrequest/{public_id}/",
        headers=_headers(token),
        timeout=30,
    )
    if r.status_code != 200:
        raise ValidationServiceError(
            f"Failed to fetch validation status: {r.status_code}",
            status_code=r.status_code,
        )
    return r.json()


def get_model(model_public_id: str, token: str = None) -> dict:
    """Fetch model summary by public ID."""
    r = requests.get(
        f"{VALIDATION_API_URL}/model/{model_public_id}/",
        headers=_headers(token),
        timeout=30,
    )
    if r.status_code != 200:
        raise ValidationServiceError(
            f"Failed to fetch model: {r.status_code}",
            status_code=r.status_code,
        )
    return r.json()


def list_validation_tasks(request_public_id: str, token: str = None) -> list:
    """List validation tasks for a given request."""
    r = requests.get(
        f"{VALIDATION_API_URL}/validationtask/",
        headers=_headers(token),
        params={"validation_request_public_id": request_public_id},
        timeout=30,
    )
    if r.status_code != 200:
        raise ValidationServiceError(
            f"Failed to fetch tasks: {r.status_code}",
            status_code=r.status_code,
        )
    data = r.json()
    return data.get("results", data) if isinstance(data, dict) else data


def summarize_validation(model_data: dict) -> dict:
    """Distill model validation into simple pass/fail statuses.

    Returns:
        {
            "schema": True/False,
            "syntax": True/False,
            "normative": True/False,
            "industry_practices": True/False,
        }

    Status codes from the API:
        'v' = valid, 'w' = warning (treated as pass),
        'i' = invalid, 'n' = not applicable, '-' = not run
    """
    failing = {'i'}

    status_ia = model_data.get('status_ia', '-')
    status_ip = model_data.get('status_ip', '-')
    status_schema = model_data.get('status_schema', '-')
    status_syntax = model_data.get('status_syntax', '-')
    status_ip_practices = model_data.get('status_industry_practices', '-')

    return {
        'schema': status_schema not in failing,
        'syntax': status_syntax not in failing,
        'normative': status_ia not in failing and status_ip not in failing,
        'industry_practices': status_ip_practices not in failing,
    }


def list_validation_outcomes(task_public_id: str, token: str = None) -> list:
    """List validation outcomes for a given task."""
    r = requests.get(
        f"{VALIDATION_API_URL}/validationoutcome/",
        headers=_headers(token),
        params={"validation_task_public_id": task_public_id},
        timeout=30,
    )
    if r.status_code != 200:
        raise ValidationServiceError(
            f"Failed to fetch outcomes: {r.status_code}",
            status_code=r.status_code,
        )
    data = r.json()
    return data.get("results", data) if isinstance(data, dict) else data


# ── Result storage (Vercel Blob with local fallback) ────────────

BLOB_TOKEN = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
BLOB_API_URL = "https://blob.vercel-storage.com"

# Local fallback (used when BLOB_READ_WRITE_TOKEN is not set)
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation_results")


def _safe_stem(file_name: str, commit_sha: str) -> str:
    """Build a safe filename stem from file name and commit."""
    safe_name = file_name.replace("/", "_").replace("\\", "_")
    safe_sha = commit_sha.replace("/", "_").replace("\\", "_").replace("..", "_")
    return f"{safe_sha}_{safe_name}"


def _blob_key(file_name: str, commit_sha: str, ext: str = ".json") -> str:
    """Build the blob path key."""
    return f"validation-results/{_safe_stem(file_name, commit_sha)}{ext}"


def _put_blob(key: str, data: bytes, content_type: str = "application/json") -> dict:
    """Upload bytes to Vercel Blob. Returns blob metadata including 'url'."""
    r = requests.put(
        f"{BLOB_API_URL}/{key}",
        headers={
            "Authorization": f"Bearer {BLOB_TOKEN}",
            "content-type": content_type,
            "x-api-version": "7",
            "x-vercel-blob-access": "private",
        },
        data=data,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _find_blob(pathname: str) -> Optional[dict]:
    """Find a blob by its logical pathname. Returns the match or None.

    Vercel Blob adds a random suffix to the stored URL but keeps the original
    pathname intact, so we search by the stem prefix and then filter by exact
    pathname match.
    """
    # Search by stem (without extension) to get candidates
    stem = pathname.rsplit(".", 1)[0] if "." in pathname else pathname
    r = requests.get(
        BLOB_API_URL,
        headers={"Authorization": f"Bearer {BLOB_TOKEN}", "x-api-version": "7"},
        params={"prefix": stem, "limit": 10},
        timeout=15,
    )
    r.raise_for_status()
    for blob in r.json().get("blobs", []):
        if blob.get("pathname") == pathname:
            return blob
    return None


def load_result(file_name: str, commit_sha: str) -> Optional[dict]:
    """Load a previously stored validation result, or None if not found."""
    if not BLOB_TOKEN:
        path = os.path.join(RESULTS_DIR, _safe_stem(file_name, commit_sha) + ".json")
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    blob = _find_blob(_blob_key(file_name, commit_sha, ".json"))
    if not blob:
        return None
    r = requests.get(blob["url"], headers={"Authorization": f"Bearer {BLOB_TOKEN}"}, timeout=15)
    r.raise_for_status()
    return r.json()


def load_bcf(file_name: str, commit_sha: str) -> Optional[bytes]:
    """Load a previously stored BCF file, or None if not found."""
    if not BLOB_TOKEN:
        path = os.path.join(RESULTS_DIR, _safe_stem(file_name, commit_sha) + ".bcf")
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    blob = _find_blob(_blob_key(file_name, commit_sha, ".bcf"))
    if not blob:
        return None
    r = requests.get(blob["url"], headers={"Authorization": f"Bearer {BLOB_TOKEN}"}, timeout=30)
    r.raise_for_status()
    return r.content


def save_result(file_name: str, commit_sha: str, summary: dict) -> dict:
    """Store validation result JSON and BCF.

    When BLOB_READ_WRITE_TOKEN is set, uploads to Vercel Blob and returns
    {'json_url': ..., 'bcf_url': ...} with public URLs the frontend can use.
    Otherwise falls back to local disk.
    """
    entry = {
        "file_name": file_name,
        "commit": commit_sha,
        "schema": summary["schema"],
        "syntax": summary["syntax"],
        "normative": summary["normative"],
        "industry_practices": summary["industry_practices"],
    }

    from bcf_converter import validation_to_bcf
    bcf_bytes = validation_to_bcf(file_name, entry, commit=commit_sha)

    if not BLOB_TOKEN:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        json_path = os.path.join(RESULTS_DIR, _safe_stem(file_name, commit_sha) + ".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2)
        bcf_path = os.path.join(RESULTS_DIR, _safe_stem(file_name, commit_sha) + ".bcf")
        with open(bcf_path, "wb") as f:
            f.write(bcf_bytes)
        return {"json_url": json_path, "bcf_url": bcf_path}

    json_blob = _put_blob(
        _blob_key(file_name, commit_sha, ".json"),
        json.dumps(entry).encode(),
        content_type="application/json",
    )
    bcf_blob = _put_blob(
        _blob_key(file_name, commit_sha, ".bcf"),
        bcf_bytes,
        content_type="application/octet-stream",
    )

    print(f"[validate] Blob JSON: {json_blob.get('url', '')}", flush=True)
    print(f"[validate] Blob BCF:  {bcf_blob.get('url', '')}", flush=True)

    return {"json_url": json_blob.get("url", ""), "bcf_url": bcf_blob.get("url", "")}


def validate_and_store(file_bytes: bytes, file_name: str, commit_sha: str, token: str = None, poll_interval: int = 5) -> dict:
    """Full validation flow: submit, poll, summarize, save, return.

    Returns the stored result dict:
        { "file_name": ..., "commit": ..., "schema": bool, "syntax": bool,
          "normative": bool, "industry_practices": bool }
    """
    import time

    print(f"[validate] {file_name} @ {commit_sha[:10]}...", flush=True)

    # Check cache first
    cached = load_result(file_name, commit_sha)
    if cached:
        print(f"[validate] Cache hit — returning stored result", flush=True)
        return cached

    # Submit
    result = submit_validation(file_bytes, file_name, token=token)
    print(f"[validate] Submitted: {result.public_id}", flush=True)

    # Poll until done
    while True:
        info = get_validation_status(result.public_id, token=token)
        status = info["status"]
        progress = info.get("progress", 0)
        print(f"[validate]   {status} ({progress}%)", flush=True)
        if status not in ("PENDING", "INITIATED", "PROCESSING"):
            break
        time.sleep(poll_interval)

    # Get model and summarize
    model_id = info.get("model_public_id")
    if not model_id:
        raise ValidationServiceError("Validation completed but no model was returned.")

    model = get_model(model_id, token=token)
    summary = summarize_validation(model)
    print(f"[validate] Done — schema:{summary['schema']} syntax:{summary['syntax']} normative:{summary['normative']} industry:{summary['industry_practices']}", flush=True)

    # Save and return
    urls = save_result(file_name, commit_sha, summary)
    print(f"[validate] Saved: {urls}", flush=True)
    result = load_result(file_name, commit_sha)
    if result and BLOB_TOKEN:
        result["bcf_url"] = urls.get("bcf_url", "")
    return result


if __name__ == "__main__":
    import sys
    import time

    if len(sys.argv) < 2:
        print("Usage: python validation_service.py /path/to/file.ifc [commit_sha]")
        sys.exit(1)

    filepath = sys.argv[1]
    commit_sha = sys.argv[2] if len(sys.argv) > 2 else "local"
    file_name = filepath.rsplit("/", 1)[-1]

    # Check if already validated
    cached = load_result(file_name, commit_sha)
    if cached:
        print(f"Already validated (cached):")
        print(json.dumps(cached, indent=2))
        sys.exit(0)

    print(f"Uploading {filepath}...")
    with open(filepath, "rb") as f:
        result = submit_validation(f.read(), file_name)

    print(f"Submitted: {result.public_id}")

    # Poll until done
    while True:
        info = get_validation_status(result.public_id)
        status = info["status"]
        progress = info.get("progress", 0)
        print(f"  {status} ({progress}%)")
        if status not in ("PENDING", "INITIATED", "PROCESSING"):
            break
        time.sleep(5)

    # Fetch model and show simple summary
    model_id = info.get("model_public_id")
    if not model_id:
        print("\nNo model returned.")
        sys.exit(1)

    model = get_model(model_id)
    summary = summarize_validation(model)

    # Save to JSON
    path = save_result(file_name, commit_sha, summary)
    print(f"\nSaved to {path}")

    print(f"\n{'='*40}")
    print(f"File:                {file_name}")
    print(f"Commit:              {commit_sha}")
    print(f"Schema:              {model.get('schema')}")
    print(f"{'='*40}")
    print(f"  schema:              {'PASS' if summary['schema'] else 'FAIL'}")
    print(f"  syntax:              {'PASS' if summary['syntax'] else 'FAIL'}")
    print(f"  normative:           {'PASS' if summary['normative'] else 'FAIL'}")
    print(f"  industry_practices:  {'PASS' if summary['industry_practices'] else 'FAIL'}")
    print(f"{'='*40}")
