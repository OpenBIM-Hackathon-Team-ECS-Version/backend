"""Convert validation results to BCF files.

Takes validation summary dicts (schema/syntax/normative/industry_practices)
and produces BCF 2.1 files with one topic per failed check.
"""

import os
import tempfile
from pathlib import Path

from bcf.v2.bcfxml import BcfXml


VALIDATION_CHECKS = {
    "schema": {
        "title": "Schema validation failed",
        "description": "The IFC file does not conform to the expected IFC schema definition.",
    },
    "syntax": {
        "title": "Syntax validation failed",
        "description": "The IFC file contains syntax errors in its STEP encoding.",
    },
    "normative": {
        "title": "Normative rules validation failed",
        "description": (
            "The IFC file violates normative (mandatory) rules "
            "from the IFC specification (integrity & appearance / integrity & preservation)."
        ),
    },
    "industry_practices": {
        "title": "Industry practices validation failed",
        "description": "The IFC file does not follow recommended industry practices.",
    },
}


def validation_to_bcf(
    file_name: str,
    summary: dict,
    commit: str = "unknown",
    author: str = "validation-service",
) -> bytes:
    """Create a BCF file from a validation summary.

    Args:
        file_name: Name of the validated IFC file.
        summary: Dict with boolean keys: schema, syntax, normative, industry_practices.
        commit: Git commit SHA or 'local'.
        author: Author string for the BCF topics.

    Returns:
        Raw bytes of the .bcf (ZIP) file.
    """
    project_name = f"Validation: {file_name}"
    if commit and commit != "unknown":
        project_name += f" ({commit[:8]})"

    bcf = BcfXml.create_new(project_name)

    failures = []
    passes = []
    for check, info in VALIDATION_CHECKS.items():
        passed = summary.get(check, True)
        if not passed:
            failures.append(check)
            description = (
                f"{info['description']}\n\n"
                f"File: {file_name}\n"
                f"Commit: {commit}"
            )
            bcf.add_topic(
                title=f"{info['title']} — {file_name}",
                description=description,
                author=author,
                topic_type="Error",
                topic_status="Open",
            )
        else:
            passes.append(check)

    # If everything passed, add a single informational topic
    if not failures:
        checks_str = ", ".join(passes)
        bcf.add_topic(
            title=f"All validations passed — {file_name}",
            description=(
                f"All checks passed: {checks_str}\n\n"
                f"File: {file_name}\n"
                f"Commit: {commit}"
            ),
            author=author,
            topic_type="Information",
            topic_status="Closed",
        )

    # Save to a temp file and read bytes
    with tempfile.NamedTemporaryFile(suffix=".bcf", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        bcf.save(tmp_path)
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)


def validation_to_bcf_file(
    file_name: str,
    summary: dict,
    output_path: str | Path,
    commit: str = "unknown",
    author: str = "validation-service",
) -> Path:
    """Create a BCF file on disk from a validation summary.

    Returns the output Path.
    """
    output_path = Path(output_path)
    data = validation_to_bcf(file_name, summary, commit, author)
    output_path.write_bytes(data)
    return output_path
