# e2e fixtures

Generated, untracked data for the seed-dependent Playwright specs. `compare-seed.json` is written by
`tools/VamsCLI/examples/seed_compare_smoke.py` and consumed through the `E2E_COMPARE_SEED` environment
variable (the absolute path to the JSON). Specs skip, with a reason, when the variable is unset or the
file is missing — no seed id is ever hardcoded in a tracked spec.

`viewer-samples.json` is the one tracked file here. It names the sample files the viewer render checks
in `viewers.spec.ts` open: each entry's URL, SHA-256, size, licence and source, verified before it was
added, and how the derived formats are made from it. The samples themselves are downloaded at test time
and never committed. `e2e/support/prepareSplatSamples.mjs` downloads the Gaussian splat sample, derives
the `.ply`, SPZ version 3 and 4, `.splat` and `.sog` files, and uploads them with `vamscli` when given
`--database` and `--asset`.
