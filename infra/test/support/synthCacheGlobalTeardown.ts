/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** Jest `globalTeardown`: remove this run's synth cache directory (see `synthCache.ts`). */

import * as fs from "fs";
import { SYNTH_CACHE_DIR_ENV } from "./synthCache";

export default function synthCacheGlobalTeardown(): void {
    const dir = process.env[SYNTH_CACHE_DIR_ENV];
    if (!dir || process.env.VAMS_KEEP_TEST_ASSEMBLIES) return;
    try {
        fs.rmSync(dir, { recursive: true, force: true });
    } catch {
        /* a leftover directory is never a test failure; the next run's setup sweeps it */
    }
}
