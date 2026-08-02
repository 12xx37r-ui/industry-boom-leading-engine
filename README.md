# Industry Boom Leading Engine V1.0.0

V1.0 freezes the V0.9.1 scoring model and runs two separate checks without retuning weights.

1. **AI 2022 locked replay**: answers whether the frozen model still detects the AI-compute precursor. This is reported separately because an AI_2022 benchmark already existed in earlier project files, so it is not counted as a fully independent holdout.
2. **2023H1 external walk-forward holdout**: the official V1 independent gate, using a later point-in-time seed and a separately sealed seven-theme cohort.

## Fixed architecture

- PC: download official SEC Financial Statement Data Sets and arXiv counts once, then create point-in-time JSON seeds.
- GitHub Actions: no SEC, FMP, or other external API calls. It verifies hashes, calculates scores, evaluates both panels, and exports JSON.
- Google Apps Script: display-only. It must not recalculate scores.

## Official independent panel

- Point-in-time date: `2023-06-30`
- Positive cases: space infrastructure, data-center power/cooling, cybersecurity forward demand, defense drones
- Negative controls: premature gene-editing commercialization, autonomous-driving/lidar overexpectation, hydrogen/fuel-cell overinvestment
- Outcome observation window ends: `2025-12-31`

## AI locked replay

- Point-in-time date: `2022-10-31`
- Main target: AI compute and data-center infrastructure
- The result is useful diagnostic evidence but does **not** determine the official independent-holdout pass/fail status.

## Model freeze

`config/model_lock.json` protects all model-affecting scoring, normalization, exposure, seed-generation, holdout-design, and evaluation files with SHA-256. GitHub stops immediately if any protected file differs.

## Run order

1. Replace the repository with this complete V1.0 ZIP.
2. On Windows, run `1_BUILD_V1_HOLDOUT_SEED.bat` once.
3. Upload both generated JSON files from `UPLOAD_THIS_FOLDER_TO_GITHUB/validation_seed` into the repository's `validation_seed` folder.
4. Run GitHub Actions: `Industry Boom V1.0 Independent Walk-Forward`.
5. Download artifact: `industry-boom-v1-independent-walkforward`.

## Final status

The official V1 decision is the external panel status:

- `V1_EXTERNAL_HOLDOUT_PASSED`
- `V1_EXTERNAL_HOLDOUT_FAILED`
- `V1_EXTERNAL_HOLDOUT_INSUFFICIENT_DATA`

AI replay has separate statuses:

- `V1_AI_LOCKED_REPLAY_PASSED`
- `V1_AI_LOCKED_REPLAY_FAILED`
- `V1_AI_LOCKED_REPLAY_INSUFFICIENT_DATA`

Every status keeps `investment_use_allowed=false`. Market-pricing, portfolio-return, and transaction-cost validation remain later stages.

## Prohibited

- Do not reintroduce FMP.
- Do not call SEC directly from GitHub Actions.
- Do not change V0.9.1 weights after viewing V1 results.
- Do not alter the sealed exposure files or thresholds to force a pass.
