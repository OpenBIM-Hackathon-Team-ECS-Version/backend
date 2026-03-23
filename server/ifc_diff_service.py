"""Compute IFC diffs for two GitHub-backed model revisions."""

import json
import os
import tempfile

import ifcopenshell


EXCLUDED_FIELDS = {
    "globalid",
    "id",
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


def _entity_summary(global_id, entry_type, data, status, changed_fields=None, previous_type=None):
    """Build a compact summary for diff UI rendering."""

    data = data if isinstance(data, dict) else {}
    return {
        "globalId": global_id,
        "status": status,
        "type": entry_type,
        "previousType": previous_type,
        "name": data.get("Name"),
        "description": data.get("Description"),
        "objectType": data.get("ObjectType"),
        "tag": data.get("Tag"),
        "changedFields": changed_fields or [],
    }


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
    details_by_id = {}

    changed = []
    changes_by_id = {}
    for global_id in added:
        current_entry = current_entries[global_id]
        details_by_id[global_id] = _entity_summary(
            global_id,
            current_entry["type"],
            current_entry["data"],
            "added",
        )

    for global_id in deleted:
        previous_entry = last_entries[global_id]
        details_by_id[global_id] = _entity_summary(
            global_id,
            previous_entry["type"],
            previous_entry["data"],
            "deleted",
        )

    for global_id in sorted(current_ids & last_ids):
        previous_entry = last_entries[global_id]
        current_entry = current_entries[global_id]
        if previous_entry["signature"] == current_entry["signature"]:
            continue

        changed.append(global_id)
        changed_fields = _changed_fields(previous_entry["data"], current_entry["data"])
        changes_by_id[global_id] = {
            "type": current_entry["type"],
            "fields": changed_fields,
        }
        details_by_id[global_id] = _entity_summary(
            global_id,
            current_entry["type"],
            current_entry["data"],
            "changed",
            changed_fields=changed_fields,
            previous_type=previous_entry["type"],
        )

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
        "detailsById": details_by_id,
    }


def summarize_ifc_bytes(ifc_bytes, global_ids=None, status="current"):
    """Return compact component summaries for a GitHub-hosted IFC model."""

    entries = _load_model_snapshot(ifc_bytes)
    selected_ids = sorted(entries.keys())
    if global_ids:
        requested = {global_id for global_id in global_ids if global_id}
        selected_ids = [global_id for global_id in selected_ids if global_id in requested]

    return {
        global_id: _entity_summary(
            global_id,
            entries[global_id]["type"],
            entries[global_id]["data"],
            status,
        )
        for global_id in selected_ids
    }
