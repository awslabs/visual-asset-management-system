# VamsCLI examples

Scripts that drive `vamscli` to set up data for tests or demos. They are not part of the installed
package; run them with `python3` from the repository root. None of them embed credentials, hosts or
ids — every script takes the deployment from a `vamscli` profile you have already configured and
logged in to (`vamscli --profile <name> auth login`).

## `seed_compare_smoke.py`

Seeds the fixtures the compare-mode Playwright specs (`web/e2e/seeded.compare.spec.ts`) need and
writes a JSON fixture the specs read through `E2E_COMPARE_SEED`. Idempotent: databases and assets
are created only when missing (assets are found by name on re-runs, because asset ids are
server-generated), and the versioned file is only topped up to the minimum version count.

```bash
# once per deployment
vamscli --profile <name> auth login

# seed (safe to repeat); prints the export line for the e2e suite
python3 tools/VamsCLI/examples/seed_compare_smoke.py --profile <name>
python3 tools/VamsCLI/examples/seed_compare_smoke.py --profile <name> --dry-run   # show the CLI calls only

# run the seeded compare specs against the same deployment
export E2E_COMPARE_SEED=/abs/path/web/e2e/fixtures/compare-seed.json
cd web && E2E_BASE_URL=https://<host> E2E_USERNAME=<user> E2E_PASSWORD=<pass> \
    npx playwright test e2e/seeded.compare.spec.ts
```

What it creates (prefix `e2e-compare`, override with `--prefix`):

| Database / asset                           | Files                                                                                                 |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `e2e-compare-db-a` / `e2e-compare-asset-a` | `/notes.txt` (≥ 3 versions), `/config.json`, `/README.md`, `/pixel.png` (1×1 PNG generated in-script) |
| `e2e-compare-db-b` / `e2e-compare-asset-b` | `/notes.txt`, `/config.json` (different content, for cross-asset diffs)                               |

The `.png` exists so a spec can prove the text differ is **not** offered for a type no compare
viewer handles.
