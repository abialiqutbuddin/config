# app/schemas/validator.py
from __future__ import annotations
import json
from pathlib import Path
from fastapi import HTTPException
from jsonschema import Draft202012Validator

_schema_cache: Draft202012Validator | None = None

def _load_schema() -> Draft202012Validator:
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache

    # adjust the path if your schema lives elsewhere
    schema_path = Path(__file__).with_name("config_schema.json")

    try:
        schema_text = schema_path.read_text(encoding="utf-8")
        schema_dict = json.loads(schema_text)        # <-- parse text to dict
    except Exception as e:
        raise RuntimeError(f"Failed to load schema: {e}") from e

    try:
        _schema_cache = Draft202012Validator(schema_dict)
        return _schema_cache
    except Exception as e:
        raise RuntimeError(f"Invalid JSON schema: {e}") from e

def validate_config_or_400(body: dict) -> None:
    """
    body is the request JSON (dict). Raises HTTP 400 on validation errors.
    """
    validator = _load_schema()
    errors = sorted(validator.iter_errors(body), key=lambda e: e.path)
    if errors:
        first = errors[0]
        loc = "/".join(str(p) for p in first.path) or "(root)"
        raise HTTPException(
            status_code=400,
            detail={
                "type": "schema_validation_error",
                "at": loc,
                "message": first.message,
            },
        )