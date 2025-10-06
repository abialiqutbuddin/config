from jsonschema import Draft202012Validator
from pathlib import Path
from fastapi import HTTPException

_schema_cache = None

def _load_schema():
    global _schema_cache
    if _schema_cache is None:
        schema_path = Path(__file__).with_name("config_schema.json")
        _schema_cache = Draft202012Validator(schema_path.read_text(encoding="utf-8"))
    return _schema_cache

def validate_config_or_400(config: dict) -> None:
    validator = _load_schema()
    errors = sorted(validator.iter_errors(config), key=lambda e: e.path)
    if errors:
        first = errors[0]
        path = "/".join([str(p) for p in first.path]) or "<root>"
        msg = f"{path}: {first.message}"
        raise HTTPException(status_code=400, detail={"type": "validation_error", "message": msg})