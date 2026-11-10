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

## `seed_viewer_samples.py`

Seeds the public sample files the viewer render cases in `web/e2e/viewers.spec.ts` open, and writes
`web/e2e/fixtures/viewer-samples-seed.json` for `E2E_VIEWER_SAMPLES`. The samples, with their source,
licence, size and SHA-256, are listed in `web/e2e/fixtures/viewer-samples.json`; the script downloads
each one, refuses any that does not match, writes the generated and derived samples (a coloured OFF
cube, a text file, a CSV with a UTF-8 byte order mark, a PLY point cloud written from a LAS file) and uploads them all to one distributable asset.
Files the asset already holds are not uploaded again. The manifest schema (downloads, generated samples,
`prepend-utf8-bom` / `copy` / `utf16le-bom` / `utf16be-bom` / `utf16le` / `zip` / `splat-transform`
/ `ply-to-splat` / `ksplat` / `las-to-ply` derivations, `requiresFeature`) is described in
`web/e2e/CLAUDE.md`, "Viewer render proof"; the script validates it before it downloads anything.

```bash
python3 tools/VamsCLI/examples/seed_viewer_samples.py --profile <name>
python3 tools/VamsCLI/examples/seed_viewer_samples.py --profile <name> --cache /tmp/vams-samples   # keep downloads between runs
python3 tools/VamsCLI/examples/seed_viewer_samples.py --profile <name> --samples splat-ply,online3d-amf   # only these
python3 tools/VamsCLI/examples/seed_viewer_samples.py --profile <name> --asset-version   # + one snapshot, then a 2nd README version

export E2E_VIEWER_SAMPLES=/abs/path/web/e2e/fixtures/viewer-samples-seed.json
cd web && E2E_BASE_URL=https://<host> E2E_USERNAME=<user> E2E_PASSWORD=<pass> \
    npx playwright test e2e/viewers.spec.ts
```

It creates `e2e-viewers-db` / `e2e-viewers-samples` (override the prefix with `--prefix`). With
`--asset-version` it also takes one asset-version snapshot (when the asset has none) and uploads the
extensionless `/noext/README` once more, so the Versions tab offers that row's Compare
(`web/e2e/seeded.compare.spec.ts`, #1198); this adds versions, so run it only on a sandbox.
