from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ModelLockError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_and_verify_model_lock(root: Path, lock_file: str | Path = "config/model_lock.json") -> dict[str, Any]:
    lock_path = Path(lock_file)
    lock_path = lock_path if lock_path.is_absolute() else root / lock_path
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelLockError(f"model lock unavailable or invalid: {exc}") from exc

    expected_files = dict(payload.get("files") or {})
    if not expected_files:
        raise ModelLockError("model lock contains no protected files")

    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    for relative, expected in sorted(expected_files.items()):
        path = root / relative
        if not path.exists():
            checks.append({"file": relative, "status": "MISSING", "expected_sha256": expected})
            failures.append(f"missing:{relative}")
            continue
        actual = sha256_file(path)
        status = "MATCH" if actual == expected else "MISMATCH"
        checks.append(
            {
                "file": relative,
                "status": status,
                "expected_sha256": expected,
                "actual_sha256": actual,
            }
        )
        if status != "MATCH":
            failures.append(f"mismatch:{relative}")

    result = {
        "status": "LOCK_VERIFIED" if not failures else "LOCK_FAILED",
        "lock_schema_version": payload.get("lock_schema_version"),
        "frozen_model_version": payload.get("frozen_model_version"),
        "engine_release": payload.get("engine_release"),
        "sealed_at": payload.get("sealed_at"),
        "rule": payload.get("rule"),
        "checks": checks,
        "failures": failures,
    }
    if failures:
        raise ModelLockError("model lock verification failed: " + ", ".join(failures))
    return result
