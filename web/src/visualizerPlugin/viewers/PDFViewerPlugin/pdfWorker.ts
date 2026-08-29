/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Points pdf.js at its worker bundle. Imported for its side effect, before any document renders.
 *
 * This lives in its own module because of `import.meta.url`, which the CommonJS transform jest uses
 * cannot evaluate ("Cannot use 'import.meta' outside a module"). At module scope in the viewer it made
 * the whole component unimportable in a unit test, so the component had none. A side-effect module can
 * be replaced with `jest.mock`, which keeps this file from being compiled at all under test while
 * leaving production behaviour identical.
 *
 * `new URL(..., import.meta.url)` is what makes the worker a tracked bundle asset rather than a runtime
 * path lookup; the emitted file keeps its `.mjs` extension, which is why the deployment has to declare
 * a JavaScript Content-Type for it (see ES_MODULE_CONTENT_TYPE in the static-web constructs).
 */

import { pdfjs } from "react-pdf";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
    "pdfjs-dist/build/pdf.worker.min.mjs",
    import.meta.url
).toString();
