/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Bundle Monaco locally instead of loading it from a CDN.
 *
 * By default @monaco-editor/react fetches the Monaco runtime from a public CDN (jsDelivr),
 * which the VAMS Content Security Policy blocks (`script-src 'self' ...`) — so the editor
 * never initializes in a deployed build. This module points the loader at the locally
 * installed `monaco-editor` package and wires Monaco's web workers through Vite's `?worker`
 * imports, so nothing is fetched from an external origin.
 *
 * Import this module once for its side effects before the editor mounts (ConfigEditor does).
 */
import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";

// Vite bundles each worker as a local module (served from same-origin), satisfying CSP.
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import CssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import HtmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import TsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";

(self as any).MonacoEnvironment = {
    getWorker(_workerId: string, label: string) {
        switch (label) {
            case "json":
                return new JsonWorker();
            case "css":
            case "scss":
            case "less":
                return new CssWorker();
            case "html":
            case "handlebars":
            case "razor":
                return new HtmlWorker();
            case "typescript":
            case "javascript":
                return new TsWorker();
            default:
                return new EditorWorker();
        }
    },
};

// Use the bundled monaco instead of the CDN copy. Guarded so a mocked @monaco-editor/react
// (unit tests) without a `loader` export doesn't throw when this module is imported.
if (loader && typeof loader.config === "function") {
    loader.config({ monaco });
}
