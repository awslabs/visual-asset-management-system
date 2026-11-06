# Potree Viewer Custom Installation

## Overview

This directory builds the Potree point cloud viewer from source. The web app loads the built library from `web/public/viewers/potree_libs/` at runtime (`PotreeViewerPlugin/dependencies.ts`) instead of bundling it.

## Pinned version

| Item       | Value                                                                |
| :--------- | :------------------------------------------------------------------- |
| Repository | [potree/potree](https://github.com/potree/potree) (`develop` branch) |
| Commit     | `5636cd47` (2026-01-08): the `1.8.2` tag plus two later code fixes   |
| VAMS patch | `Potree-Fork_5636cd47.patch`                                         |
| License    | BSD-2-Clause (upstream); VAMS modifications Apache-2.0               |

The patch file name must match the pinned commit. When the commit moves, rebase the patch onto it and rename it in the same change.

## What the VAMS patch changes

-   `loadPointCloud(path, name, requestHeaders, callback)` takes request headers, passes them to the Potree 2.0 `OctreeLoader` (`metadata.json`, `hierarchy.bin`, `octree.bin` requests), and rejects its promise when the load fails, so the viewer can report why.
-   Removes Potree's global CSS rules (`a`, `canvas`, `body`) that would restyle the VAMS page.
-   Removes the sidebar header and About section, and silences Potree's console logging.
-   Pins the render canvas to `left: 0%`.

VAMS reads only the Potree 2.0 octree format that the Point Cloud Potree Viewer pipeline (`app.pipelines.usePreviewPcPotreeViewer`) writes. The EPT and COPC loaders upstream added in 1.8.2 are built but not used.

## Installation script (`potreeInstall.js`)

Run by the web `postinstall` script when `potree-viewer` is enabled in `viewerConfig.json`:

1. Removes the previous clone (`customInstalls/potree/source`) and output (`public/viewers/potree_libs`).
2. Clones potree/potree, checks out the pinned commit, and applies the patch (`--ignore-whitespace --whitespace=fix`).
3. Runs `npm install --min-release-age=7` and `npm run build` in the clone. The clone sits outside `web/`, so `web/.npmrc` does not reach it and the release-age floor is passed on the command line.
4. Copies `build/potree` and the upstream `libs/` folder into `public/viewers/potree_libs/`.

## Vendored libraries

Potree keeps third-party code in its `libs/` folder, and step 4 ships it. Since 1.8.2 that includes `libs/copc`: a webpack bundle of [copc](https://github.com/connormanning/copc.js) 0.0.5-alpha.1 (MIT) with the [cross-fetch](https://github.com/lquixada/cross-fetch) 3.1.5 browser ponyfill (MIT), plus the [laz-perf](https://github.com/hobuinc/laz-perf) 0.0.5 WebAssembly decoder (Apache-2.0), which the build also copies into `potree/workers/`. VAMS does not load them (it reads only Potree 2.0 octrees), but they ship, so `NOTICE.md` lists each one. The versions were identified by comparing the files with the npm releases. `potreeInstall.js` checks the files' SHA-256 (`bundledLibraryFiles`) and stops when a commit move changes them; identify the new versions, update the `NOTICE.md` rows and `bundledLibraries`, then the hashes.

Any clone, checkout, patch, install, build, copy or vendored-library check error stops the install with exit code 1. An unpatched build would send no auth header and never report a failed load, so it is not shipped.
