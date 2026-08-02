# Industry Boom Leading Engine V0.8.10

V0.8.10 fixes the V0.8.9 offline-seed failure where the strict fiscal-quarter filter left only 14 of 35 historically eligible companies.

## What changed

- Reliable quarter-by-quarter series are still preferred.
- When quarterly facts are incomplete or fiscal periods cannot be aligned safely, the engine uses consolidated annual SEC facts from the same XBRL tag.
- Annual flows are divided into four equal quarterly proxy points solely to preserve annual year-over-year growth for the frozen scoring model.
- The annual fallback never mixes XBRL tags and never subtracts facts across fiscal years.
- Revenue series with economically implausible annual growth are still rejected.
- The seed audit records whether each metric came from strict quarterly data or the annual fallback.
- Existing six SEC ZIP files and cached arXiv counts are reused. No new SEC download is needed.
- GitHub validation remains zero-network.

## Run order

1. Extract this ZIP over the same local folder used for V0.8.9.
2. Keep `local_sec_data` and the existing `validation_seed/sec_fsds_fy2021.json`.
3. Run `1_BUILD_OFFLINE_SEED.bat`.
4. Upload `UPLOAD_THIS_FOLDER_TO_GITHUB/validation_seed` to the repository root, replacing the old seed.
5. Upload the full V0.8.10 source to GitHub.
6. Run **Industry Boom Offline Holdout V0.8.10**.
7. Download Artifact `industry-boom-global-holdout-v0.8.10`.

Required GitHub path:

```text
validation_seed/sec_fsds_fy2021.json
```
