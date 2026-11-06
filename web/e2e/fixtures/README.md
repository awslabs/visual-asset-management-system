# e2e fixtures

Generated, untracked data for the seed-dependent Playwright specs. `compare-seed.json` is written by
`tools/VamsCLI/examples/seed_compare_smoke.py` and consumed through the `E2E_COMPARE_SEED` environment
variable (the absolute path to the JSON). Specs skip, with a reason, when the variable is unset or the
file is missing — no seed id is ever hardcoded in a tracked spec.

`viewer-samples.json` is the one tracked file here: the public sample files the viewer render smoke
(`viewers.spec.ts`) uses, each with its download URL (pinned to a commit), sha256, size, licence and
source. Download the files at test time, check the checksum, and upload them with vamscli to one
distributable asset (then point `E2E_VIEWER_ASSET` at it). Never commit the sample files themselves,
and check a sample's licence at its source before adding it.
