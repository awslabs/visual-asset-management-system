# That Open Engine (web-ifc) IFC/BIM Viewer — Custom Install

This directory vendors the [That Open Engine](https://github.com/ThatOpen/engine_components) IFC/BIM
viewer libraries into a single self-contained UMD bundle for the VAMS dynamic viewer system. Nothing
here is added to the core `web/package.json` dependencies.

## What it does

`thatOpenWebIfcInstall.js` (run from the root `web` `postinstall` chain):

1. Skips entirely if `thatopenwebifc-viewer` is disabled in
   `src/visualizerPlugin/config/viewerConfig.json` (via `checkViewerEnabled`).
2. Runs `npm install` in this directory.
3. Runs `webpack` to bundle `@thatopen/components`, `@thatopen/components-front`,
   `@thatopen/fragments`, `web-ifc`, `three`, `camera-controls`, and `fflate` into
   `dist/thatopenwebifc.min.js` as the UMD global `window.ThatOpenWebIfcBundle`.
4. Copies the bundle plus the web-ifc `*.wasm` binaries and the Fragments/web-ifc worker scripts into
   `web/public/viewers/thatopenwebifc/`, served same-origin at `/viewers/thatopenwebifc/`.

## Runtime

The viewer's dependency manager injects `/viewers/thatopenwebifc/thatopenwebifc.min.js` via a
`<script>` tag and reads `window.ThatOpenWebIfcBundle`. The `web-ifc` WASM path and the Fragments
worker URL both point at `/viewers/thatopenwebifc/`.

## Licenses

-   `@thatopen/components`, `@thatopen/components-front`, `@thatopen/fragments`: **MIT**
-   `three`, `camera-controls`, `fflate`: **MIT**
-   `web-ifc`: **MPL-2.0** (Mozilla Public License 2.0). File-level copyleft; used here as an unmodified
    dependency, which imposes no obligations on VAMS's own source. Only modifications to web-ifc's own
    files would need to be shared.

## Cross-origin isolation

The multithreaded `web-ifc-mt.wasm` build uses `SharedArrayBuffer`, which requires cross-origin
isolation. VAMS already ships `web/public/coi-serviceworker.js` and sets COOP/COEP dev headers in
`vite.config.ts`. If isolation is unavailable, web-ifc transparently falls back to the single-threaded
`web-ifc.wasm` (slower parse, no hard failure).
