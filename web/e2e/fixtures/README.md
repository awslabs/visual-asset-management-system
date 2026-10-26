# e2e fixtures

Generated, untracked data for the seed-dependent Playwright specs. `compare-seed.json` is written by
`tools/VamsCLI/examples/seed_compare_smoke.py` and consumed through the `E2E_COMPARE_SEED` environment
variable (the absolute path to the JSON). Specs skip, with a reason, when the variable is unset or the
file is missing — no seed id is ever hardcoded in a tracked spec.
