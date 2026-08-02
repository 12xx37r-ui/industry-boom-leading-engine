# Industry Boom Leading Engine V0.8.6

V0.8.6 removes FMP and the SEC Company Facts API from the global holdout path.
The workflow downloads the SEC's official quarterly Financial Statement Data Sets,
filters every record by filing date and statement period, builds a compact seed, and
then runs the holdout without further financial-provider calls.

## Why this version exists

- FMP Basic returned only a restricted symbol set.
- SEC Company Facts and companyfacts.zip were blocked from GitHub-hosted runner IPs.
- The SEC Financial Statement Data Sets are static quarterly ZIP downloads intended
  for bulk analysis, so they avoid 37 per-company API calls.

## Point-in-time rule

The global cohort cutoff is `2022-04-30`.
Only records whose filing date and financial period are both on or before the cutoff
are retained. The seed is built from SEC datasets `2021q1` through `2022q2`.
Current companies that had no eligible SEC filing by the cutoff are excluded from the
historical denominator rather than being treated as missing 2021 companies.

## Run

1. Upload the complete ZIP contents to the repository.
2. Keep the existing repository variable `SEC_USER_AGENT` in the form:

   `IndustryBoomLeadingEngine/0.8.6 your-real-email@example.com`

3. Run:

   `Actions -> Industry Boom Global Holdout V0.8.6 -> Run workflow`

4. Leave `refresh_sec_fsds` set to `false` for the first run.

The first run downloads six large SEC quarterly archives and can take time. Later runs
restore the compact seed and downloaded archives from the Actions cache.

## Outputs

- `industry-boom-global-holdout-v0.8.6`
- `industry-boom-sec-fsds-seed-v0.8.6`
- `industry-boom-sec-fsds-diagnostics-v0.8.6`

Expected result statuses:

- `PASSED_V086_GLOBAL_HOLDOUT`
- `FAILED_V086_GLOBAL_HOLDOUT`
- `INSUFFICIENT_V086_GLOBAL_HOLDOUT`

A provider or download failure is written as an insufficient-data result instead of
raising a long Python traceback after dozens of repeated requests.
