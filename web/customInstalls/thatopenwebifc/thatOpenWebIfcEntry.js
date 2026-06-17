/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Webpack entry for the That Open Engine IFC/BIM viewer bundle.
// Everything imported here is bundled into a single UMD global
// (window.ThatOpenWebIfcBundle) by webpack.config.js. The React viewer
// component consumes ONLY this global — it never imports these ESM packages
// directly — which keeps all third-party libraries out of the core web build
// (mirrors how the threejs customInstall exposes window.THREEBundle).

import * as THREE from "three";
import * as OBC from "@thatopen/components";
import * as OBF from "@thatopen/components-front";
import * as FRAGS from "@thatopen/fragments";
import * as WEBIFC from "web-ifc";
import { unzipSync } from "fflate";

// --- Force web-ifc into single-threaded mode ---------------------------------
// web-ifc's IfcAPI.Init(handler, forceSingleThread = false) loads the
// MULTITHREADED build (web-ifc-mt.wasm) whenever `self.crossOriginIsolated` is
// true. VAMS ships a COI service worker, so crossOriginIsolated is true for the
// whole app — which means web-ifc would boot its Emscripten pthread pool.
//
// That pool spawns its workers via `new Worker(_scriptName, { type: "module" })`
// where `_scriptName` is normally `import.meta.url`. Inside THIS webpack UMD
// bundle there is no module URL, so `_scriptName` is `undefined`; the browser
// requests `/undefined`, the SPA host returns index.html, and the worker dies
// with "Uncaught SyntaxError: Unexpected number" — one error per pool thread —
// leaving IfcImporter.process() hung forever (viewer stuck on "Parsing IFC
// model"). @thatopen/fragments' IfcImporter calls `ifcApi.Init()` with no args
// and exposes no flag to override this, so we patch the prototype here.
//
// Forcing single-thread uses the standalone web-ifc.wasm (no pthread workers),
// which is correct and self-contained for a bundled viewer. It is marginally
// slower to parse very large IFC files but does not require SharedArrayBuffer
// or any worker URL resolution. The app-wide COI worker is left untouched so
// other WASM viewers (Needle USD, Cesium) keep their multithreaded behavior.
if (WEBIFC && WEBIFC.IfcAPI && WEBIFC.IfcAPI.prototype && !WEBIFC.IfcAPI.prototype.__vamsForceST) {
    const originalInit = WEBIFC.IfcAPI.prototype.Init;
    WEBIFC.IfcAPI.prototype.Init = function patchedInit(customLocateFileHandler) {
        // Always pass forceSingleThread = true, regardless of how callers (e.g.
        // @thatopen/fragments' IfcImporter) invoke Init.
        return originalInit.call(this, customLocateFileHandler, true);
    };
    WEBIFC.IfcAPI.prototype.__vamsForceST = true;
}
// -----------------------------------------------------------------------------

// Export everything the viewer needs as a single default object.
export default {
    THREE, // three.js (bundled here, isolated from the app's other three bundles)
    OBC, // @thatopen/components — Worlds, IfcLoader, FragmentsManager, Clipper, Hider, Classifier, etc.
    OBF, // @thatopen/components-front — Highlighter, measurements, ClipEdges, Plans, post-production
    FRAGS, // @thatopen/fragments — Fragments model runtime
    WEBIFC, // web-ifc — IFC schema enums/constants (e.g., schema names)
    unzipSync, // fflate — used to extract the .ifc entry from a .ifczip archive
};
