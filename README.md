# Industry Boom Leading Engine V1.0.0

V1.0.0 is the first independent walkforward validation package for the frozen V0.9.1 scoring model.

## What is frozen

The following V0.9.1 scoring files are SHA-256 locked before validation:

- `src/ible/global_validation.py`
- `src/ible/analytics/exposure_scoring.py`
- `src/ible/analytics/scoring.py`

Any change causes `MODEL_LOCK_MISMATCH` and stops validation.

## Independent test cohort

Two point-in-time snapshots are built locally:

- `WF_2019H1`: cutoff 2019-04-30
- `WF_2019H2`: cutoff 2019-10-31

New themes not used to tune V0.9.1:

- Semiconductor manufacturing equipment
- Digital payments
- Streaming media
- Gene editing therapeutics control
- Online lending control
- Ride-hailing control at the second snapshot

## Run order

1. Extract this ZIP on the Windows PC.
2. Keep the existing `local_sec_data` folder if present.
3. Run `1_BUILD_WALKFORWARD_SEED.bat`.
4. Upload `UPLOAD_THIS_FOLDER_TO_GITHUB/validation_seed/walkforward` to the repository top level.
5. Upload the V1.0.0 project files to the repository.
6. Ensure `.github/workflows/run_independent_walkforward.yml` exists.
7. Run `Industry Boom Independent Walkforward V1.0.0` in GitHub Actions.
8. Download the artifact `industry-boom-independent-walkforward-v1.0.0`.

GitHub Actions performs no SEC, FMP, or arXiv network calls. It only validates the checked-in seed and runs the frozen model.
