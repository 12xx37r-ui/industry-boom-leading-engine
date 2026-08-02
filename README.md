# Industry Boom Leading Engine V0.8.8

V0.8.8 keeps the zero-network GitHub workflow from V0.8.7 and fixes the SEC FSDS quarter-normalization defect discovered in the first valid global holdout.

## What changed

- Never mixes different XBRL tags inside one quarterly series.
- Filters non-empty `coreg` rows so co-registrant values cannot overwrite consolidated figures.
- Derives Q2/Q3/Q4 only from the same tag and adjacent fiscal-quarter cumulative facts.
- Rejects negative revenue, CAPEX, and R&D values created by incompatible cumulative subtraction.
- Stores a per-company/per-metric normalization audit in the seed.
- Uses robust cross-company aggregation so one extreme company cannot dominate a theme.
- Shrinks margin scores toward neutral when margin coverage is low.
- GitHub Actions still performs no SEC, FMP, or arXiv network calls.

## Why V0.8.7 results must not be used

The V0.8.7 data gate passed, but several outputs were economically impossible, including multi-hundred-percent theme revenue changes. The cause was quarter series built from different revenue concepts and cumulative facts. V0.8.8 changes only normalization and generic robustness rules; the holdout labels and pass criteria are unchanged.

## Run order

1. Extract this ZIP over the existing repository folder.
2. Run `1_BUILD_OFFLINE_SEED.bat` on the same PC folder used for V0.8.7.
3. Existing files under `local_sec_data` are reused, so the six SEC ZIP files are not downloaded again.
4. Upload `UPLOAD_THIS_FOLDER_TO_GITHUB/validation_seed` to the repository top level, replacing the old `validation_seed/sec_fsds_fy2021.json`.
5. Upload the complete V0.8.8 source to GitHub.
6. Run **Industry Boom Offline Holdout V0.8.8**.
7. Download Artifact `industry-boom-global-holdout-v0.8.8`.

## Required GitHub path

```text
validation_seed/sec_fsds_fy2021.json
```

The V0.8.7 seed is rejected by schema and normalization-version checks. This prevents the old malformed quarter series from being scored accidentally.
