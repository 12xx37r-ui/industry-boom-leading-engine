from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from ible.offline_seed_builder import (
    OfflineSeedError,
    build_seed,
    compute_seed_sha256,
)

VERSION = "1.0.0"
SCHEMA_VERSION = 6
MASTER_CONFIG = Path("config/walkforward_seed_requests.json")
LEGACY_REQUEST = Path("config/offline_seed_request.json")
LEGACY_SEED = Path("validation_seed/sec_fsds_fy2021.json")


def _load_master(root: Path) -> dict[str, Any]:
    path = root / MASTER_CONFIG
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OfflineSeedError(f"cannot read {path}: {exc}") from exc
    if payload.get("generator_version") != VERSION:
        raise OfflineSeedError("walkforward_seed_requests.json version mismatch")
    snapshots = payload.get("snapshots") or []
    if not isinstance(snapshots, list) or len(snapshots) < 2:
        raise OfflineSeedError("at least two walkforward snapshots are required")
    return payload


def _legacy_request(snapshot: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "cutoff",
        "periods",
        "source_url_template",
        "tickers",
        "research",
        "minimum_financial_coverage",
        "minimum_available_companies",
        "minimum_research_themes",
    }
    request = {key: snapshot[key] for key in allowed if key in snapshot}
    request["schema_version"] = 1
    # Reuse the battle-tested V0.8.10 point-in-time seed builder unchanged.
    request["generator_version"] = "0.8.10"
    return request


def _validate_single_seed(seed: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    metadata = seed.get("metadata") or {}
    status = seed.get("status") or {}
    expected_tickers = sorted(str(row["ticker"]).upper() for row in snapshot.get("tickers") or [])
    if metadata.get("version") != VERSION or metadata.get("schema_version") != SCHEMA_VERSION:
        errors.append("version/schema mismatch")
    if metadata.get("snapshot_id") != snapshot.get("id"):
        errors.append("snapshot id mismatch")
    if metadata.get("cutoff") != snapshot.get("cutoff"):
        errors.append("cutoff mismatch")
    if sorted(metadata.get("requested_tickers") or []) != expected_tickers:
        errors.append("ticker cohort mismatch")
    if status.get("status") != "READY":
        errors.append(f"status is {status.get('status')}")
    if list(status.get("periods_downloaded") or []) != list(snapshot.get("periods") or []):
        errors.append("SEC periods incomplete")
    if float(status.get("coverage_of_historically_eligible") or 0) < float(snapshot.get("minimum_financial_coverage", 0.68)):
        errors.append("financial coverage below minimum")
    if int(status.get("available") or 0) < int(snapshot.get("minimum_available_companies", 18)):
        errors.append("available company count below minimum")
    if int(status.get("research_available") or 0) < int(snapshot.get("minimum_research_themes", 5)):
        errors.append("research coverage below minimum")
    expected_hash = str(metadata.get("content_sha256") or "")
    actual_hash = compute_seed_sha256(seed)
    if not expected_hash or expected_hash != actual_hash:
        errors.append("integrity SHA-256 mismatch")
    return errors


def build_walkforward_seeds(root: Path, user_agent: str, *, refresh: bool = False) -> dict[str, Any]:
    master = _load_master(root)
    snapshots = list(master["snapshots"])
    legacy_request_path = root / LEGACY_REQUEST
    legacy_seed_path = root / LEGACY_SEED
    request_backup = legacy_request_path.read_bytes() if legacy_request_path.exists() else None
    seed_backup = legacy_seed_path.read_bytes() if legacy_seed_path.exists() else None
    built: list[dict[str, Any]] = []

    try:
        for index, snapshot in enumerate(snapshots, 1):
            snapshot_id = str(snapshot["id"])
            print(
                f"[WALKFORWARD-SEED] build {index}/{len(snapshots)} id={snapshot_id} cutoff={snapshot['cutoff']}",
                flush=True,
            )
            legacy_request_path.write_text(
                json.dumps(_legacy_request(snapshot), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            build_seed(root, user_agent, refresh=refresh, skip_research=False)
            try:
                seed = json.loads(legacy_seed_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise OfflineSeedError(f"legacy seed read failed for {snapshot_id}: {exc}") from exc

            metadata = dict(seed.get("metadata") or {})
            metadata.update(
                {
                    "schema_version": SCHEMA_VERSION,
                    "version": VERSION,
                    "snapshot_id": snapshot_id,
                    "frozen_model_version": str(master.get("frozen_model_version") or "0.9.1"),
                    "validation_role": "independent_walkforward_holdout",
                }
            )
            seed["metadata"] = metadata
            seed["metadata"]["content_sha256"] = compute_seed_sha256(seed)
            errors = _validate_single_seed(seed, snapshot)
            if errors:
                raise OfflineSeedError(f"{snapshot_id}: " + "; ".join(errors))

            target = root / str(snapshot["seed_file"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
            upload = root / "UPLOAD_THIS_FOLDER_TO_GITHUB" / str(snapshot["seed_file"])
            upload.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, upload)
            status = seed.get("status") or {}
            built.append(
                {
                    "snapshot_id": snapshot_id,
                    "seed_file": str(snapshot["seed_file"]),
                    "cutoff": snapshot["cutoff"],
                    "available": status.get("available"),
                    "historically_eligible_count": status.get("historically_eligible_count"),
                    "coverage": status.get("coverage_of_historically_eligible"),
                    "research_available": status.get("research_available"),
                    "content_sha256": seed["metadata"]["content_sha256"],
                }
            )
    finally:
        if request_backup is None:
            legacy_request_path.unlink(missing_ok=True)
        else:
            legacy_request_path.write_bytes(request_backup)
        if seed_backup is None:
            legacy_seed_path.unlink(missing_ok=True)
        else:
            legacy_seed_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_seed_path.write_bytes(seed_backup)

    # Do not include the temporary legacy seed in the upload folder.
    (root / "UPLOAD_THIS_FOLDER_TO_GITHUB" / LEGACY_SEED).unlink(missing_ok=True)

    manifest = {
        "status": "READY",
        "version": VERSION,
        "frozen_model_version": master.get("frozen_model_version"),
        "snapshot_count": len(built),
        "snapshots": built,
    }
    manifest_path = root / "validation_seed" / "walkforward" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    upload_manifest = root / "UPLOAD_THIS_FOLDER_TO_GITHUB" / "validation_seed" / "walkforward" / "manifest.json"
    upload_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_path, upload_manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return manifest


def validate_walkforward_seeds(root: Path) -> dict[str, Any]:
    master = _load_master(root)
    results: list[dict[str, Any]] = []
    all_errors: list[str] = []
    for snapshot in master["snapshots"]:
        path = root / str(snapshot["seed_file"])
        if not path.exists():
            all_errors.append(f"{snapshot['id']}: missing {snapshot['seed_file']}")
            continue
        try:
            seed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            all_errors.append(f"{snapshot['id']}: invalid JSON: {exc}")
            continue
        errors = _validate_single_seed(seed, snapshot)
        if errors:
            all_errors.extend(f"{snapshot['id']}: {error}" for error in errors)
            continue
        status = seed.get("status") or {}
        results.append(
            {
                "snapshot_id": snapshot["id"],
                "cutoff": snapshot["cutoff"],
                "available": status.get("available"),
                "coverage": status.get("coverage_of_historically_eligible"),
                "research_available": status.get("research_available"),
                "content_sha256": (seed.get("metadata") or {}).get("content_sha256"),
            }
        )
    if all_errors:
        raise OfflineSeedError("; ".join(all_errors))
    result = {
        "status": "READY",
        "version": VERSION,
        "frozen_model_version": master.get("frozen_model_version"),
        "snapshot_count": len(results),
        "snapshots": results,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or validate independent walkforward seeds")
    parser.add_argument("--root", default=".")
    parser.add_argument("--email", default="")
    parser.add_argument("--user-agent", default="")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        if args.validate_only:
            validate_walkforward_seeds(root)
            return 0
        user_agent = args.user_agent.strip() or f"IndustryBoomLeadingEngine/1.0.0 {args.email.strip()}"
        build_walkforward_seeds(root, user_agent, refresh=args.refresh)
        return 0
    except OfflineSeedError as exc:
        print(f"[WALKFORWARD-SEED-ERROR] {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
