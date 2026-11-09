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

## Versions and build notes

-   `@thatopen/components`, `@thatopen/components-front`, `@thatopen/fragments` `~3.6.0`, `web-ifc`
    `0.0.77`, and the bundle's own isolated `three` `0.184.0` (the version That Open 3.6 is tested with).
-   `web-ifc` stays at `0.0.77`. In `0.0.78` the single-threaded browser build's `StreamMeshes` binding
    takes three arguments while its JavaScript wrapper passes four, so every IFC import fails with
    `BindingError: function StreamMeshes called with 4 arguments, expected 3`
    ([ThatOpen/engine_web-ifc#2219](https://github.com/ThatOpen/engine_web-ifc/issues/2219), fixed on
    `main`, not yet released). This viewer forces the single-threaded build (see
    `thatOpenWebIfcEntry.js`), so it would hit that on every file. Move to the first release after
    `0.0.78` that carries the fix, and re-check the `IfcAPI.Init` patch in `thatOpenWebIfcEntry.js`.
-   The copy of `web-ifc.wasm`, `web-ifc-mt.wasm` and the Fragments worker
    (`@thatopen/fragments/dist/Worker/worker.min.mjs`) fails the webpack build when a file is missing,
    so a library upgrade that renames one of them cannot ship a viewer whose WASM or worker URL 404s.
-   That Open 3.5+ adds an opt-in WebGPU renderer path. It reads WebGPU-only names (`WebGPURenderer`,
    `TSL`, `NodeMaterial`, ...) from `three`, which exist only when an app aliases `three` to
    `three/webgpu`. VAMS keeps the WebGL renderer, so those accesses are feature-detected at runtime and
    `webpack.config.js` turns webpack's missing-export errors for them into filtered warnings. The
    WebGPU, TSL and TTF-font code ends up in separate lazily loaded chunks in `dist/` that the WebGL path
    never requests; the install copies them anyway with the rest of `dist/`.
-   `components-front` lazily imports the optional peer `potree-core` from its `PointCloudLoader`. The
    viewer does not use it, so `webpack.config.js` aliases `potree-core` to an empty module and the
    package is not installed. Point clouds use the VAMS Potree viewer.

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
