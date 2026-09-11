/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Point maplibre-gl at a bundled copy of its web worker.
 *
 * maplibre-gl ships the worker as a separate ES module (`maplibre-gl-worker.mjs`, which imports
 * `maplibre-gl-shared.mjs`) and by default resolves it as a sibling of the main module's
 * `import.meta.url`. Inside the Vite bundle that sibling is never emitted, so without this call
 * every map requests a worker that does not exist and renders nothing. `?worker&url` routes the
 * file through Vite's worker pipeline, producing one self-contained same-origin chunk that the
 * `worker-src 'self'` policy allows; a plain `?url` would copy the worker alone and it would fail
 * on its first import.
 *
 * Import this module once for its side effect before a map mounts (every react-map-gl consumer does).
 */
import { setWorkerUrl } from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

setWorkerUrl(maplibreWorkerUrl);
