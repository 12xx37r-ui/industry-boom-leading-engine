from __future__ import annotations

from pathlib import Path
from typing import Any

from ible.integrity import canonical_sha256, load_json


class GateReceiptError(RuntimeError):
    pass


def load_and_verify_gate_receipt(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    expected = str(payload.get("receipt_sha256") or "")
    unsigned = dict(payload)
    unsigned.pop("receipt_sha256", None)
    actual = canonical_sha256(unsigned)
    if expected != actual:
        raise GateReceiptError(f"V1.1 gate receipt SHA-256 mismatch: expected={expected or 'missing'} actual={actual}")
    if payload.get("status") != "V1_1_BLIND_THEME_HOLDOUT_PASSED":
        raise GateReceiptError("V1.1 blind holdout gate is not passed")
    if payload.get("model_lock_status") != "LOCK_VERIFIED":
        raise GateReceiptError("V1.1 model lock was not verified")
    if payload.get("investment_use_allowed") is not False:
        raise GateReceiptError("V1.1 receipt must keep investment use disabled")
    return {
        "status": "V1_1_GATE_VERIFIED",
        "receipt_sha256": expected,
        "source_artifact": payload.get("source_artifact"),
        "metrics": payload.get("metrics"),
    }
