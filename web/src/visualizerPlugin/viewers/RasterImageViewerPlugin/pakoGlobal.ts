/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `utif2` finds its zlib port with `typeof require === "function" ? require("pako") : self.pako`. In
 * the bundled decode worker `require` is not a function at that point, so the lookup falls to the
 * global and inflating a deflate-compressed TIFF fails with "Cannot read properties of undefined
 * (reading 'inflateRaw')". Importing this module before `utif2` puts the library on the global the
 * fallback reads. The evaluation order of ES module imports is what makes that work.
 */

import pako from "pako";

(globalThis as any).pako = (pako as any).default ?? pako;
