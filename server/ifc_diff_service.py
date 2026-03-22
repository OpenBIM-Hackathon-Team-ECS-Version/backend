"""Compute IFC diffs for two GitHub-backed model revisions."""

import json
import os
import tempfile

import ifcopenshell


EXCLUDED_FIELDS = {
    "globalid",
    "id",
    "objectplacement",
    "ownerhistory",
    "representation",
    "representationmaps",
    "representations",
    "step_id",
    "unitsincontext",
}


def _normalize_value(value):
    """Convert ifcopenshell values into stable JSON-friendly structures."""

    if isinstance(value, dict):
        normalized = {}
        for key in sorted(value.keys()):
            if str(key).lower() in EXCLUDED_FIELDS:
                continue
            normalized[str(key)] = _normalize_value(value[key])
        return normalized

    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    if hasattr(value, "get_info"):
        return _normalize_value(value.get_info(recursive=False))

    return str(value)


def _changed_fields(previous_data, current_data):
    """Return top-level fields whose normalized values changed."""

    keys = set(previous_data.keys()) | set(current_data.keys())
    changed = []
    for key in sorted(keys):
        if previous_data.get(key) != current_data.get(key):
            changed.append(key)
    return changed


def _load_model_snapshot(ifc_bytes):
    """Load object-definition snapshots keyed by raw IFC GlobalId."""

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as temp_file:
            temp_file.write(ifc_bytes)
            temp_path = temp_file.name

        model = ifcopenshell.open(temp_path)
        entries = {}
        for entity in model.by_type("IfcObjectDefinition"):
            global_id = getattr(entity, "GlobalId", None)
            if not global_id:
                continue

            normalized = _normalize_value(entity.get_info(recursive=True))
            if isinstance(normalized, dict):
                normalized.pop("GlobalId", None)

            entries[global_id] = {
                "type": entity.is_a(),
                "data": normalized,
                "signature": json.dumps(
                    {
                        "type": entity.is_a(),
                        "data": normalized,
                    },
                    sort_keys=True,
                    default=str,
                ),
            }

        return entries
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def diff_ifc_bytes(current_bytes, last_bytes=None):
    """Compare two IFC revisions and return highlight-ready diff data."""

    current_entries = _load_model_snapshot(current_bytes)
    last_entries = _load_model_snapshot(last_bytes) if last_bytes is not None else {}

    current_ids = set(current_entries.keys())
    last_ids = set(last_entries.keys())

    added = sorted(current_ids - last_ids)
    deleted = sorted(last_ids - current_ids)

    changed = []
    changes_by_id = {}
    for global_id in sorted(current_ids & last_ids):
        previous_entry = last_entries[global_id]
        current_entry = current_entries[global_id]
        if previous_entry["signature"] == current_entry["signature"]:
            continue

        changed.append(global_id)
        changes_by_id[global_id] = {
            "type": current_entry["type"],
            "fields": _changed_fields(previous_entry["data"], current_entry["data"]),
        }

    return {
        "summary": {
            "added": len(added),
            "changed": len(changed),
            "deleted": len(deleted),
        },
        "added": added,
        "changed": changed,
        "deleted": deleted,
        "changesById": changes_by_id,
    }
