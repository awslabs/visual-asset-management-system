# e2e fixtures

Generated, untracked data for the seed-dependent Playwright specs. `compare-seed.json` is written by
`tools/VamsCLI/examples/seed_compare_smoke.py` and consumed through the `E2E_COMPARE_SEED` environment
variable (the absolute path to the JSON). Specs skip, with a reason, when the variable is unset or the
file is missing — no seed id is ever hardcoded in a tracked spec.

`viewer-samples.json` is the one tracked file here. It lists the sample files for `viewers.spec.ts`
(URL, sha256, size, licence, source; for a derived file, the tool, version and command that make it).
Download or derive each sample at test time, check its sha256, and upload it to a distributable asset
(`E2E_VIEWER_ASSET` points the spec at that asset). Never commit the files, and add a sample only after
checking that its licence is permissive.
