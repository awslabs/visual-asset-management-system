# e2e fixtures

Generated, untracked data for the seed-dependent Playwright specs. `compare-seed.json` is written by
`tools/VamsCLI/examples/seed_compare_smoke.py` and consumed through the `E2E_COMPARE_SEED` environment
variable (the absolute path to the JSON). Specs skip, with a reason, when the variable is unset or the
file is missing — no seed id is ever hardcoded in a tracked spec.

`viewer-samples.json` is tracked source, not seed output: the manifest of public sample files (URL, size,
SHA-256, licence, source) the viewer render cases in `viewers.spec.ts` open. `tools/VamsCLI/examples/seed_viewer_samples.py`
downloads and checks each sample, uploads them to one distributable asset and writes `viewer-samples-seed.json`,
which the specs read through `E2E_VIEWER_SAMPLES`. No sample file is committed.
