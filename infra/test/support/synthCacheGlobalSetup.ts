/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Jest `globalSetup`: give this run a fresh, empty synth cache directory (see `synthCache.ts`).
 *
 * Runs once per `jest` invocation, in the parent process, before any worker starts, so the
 * environment variable set here is inherited by every worker. Also sweeps directories left by runs
 * that were killed before their teardown, identified by the parent pid in the directory name.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { SYNTH_CACHE_DIR_ENV, SYNTH_CACHE_DIR_PREFIX, SYNTH_CACHE_MODE_ENV } from "./synthCache";

function processIsAlive(pid: number): boolean {
    try {
        process.kill(pid, 0);
        return true;
    } catch (e: any) {
        return e?.code === "EPERM";
    }
}

export default function synthCacheGlobalSetup(): void {
    const tmp = fs.realpathSync(os.tmpdir());

    for (const entry of fs.readdirSync(tmp)) {
        if (!entry.startsWith(SYNTH_CACHE_DIR_PREFIX)) continue;
        const pid = Number(entry.slice(SYNTH_CACHE_DIR_PREFIX.length).split("-")[0]);
        if (!Number.isInteger(pid) || pid <= 0 || processIsAlive(pid)) continue;
        try {
            fs.rmSync(path.join(tmp, entry), { recursive: true, force: true });
        } catch {
            /* a leftover directory is never a test failure */
        }
    }

    if ((process.env[SYNTH_CACHE_MODE_ENV] ?? "").trim().toLowerCase() === "off") {
        delete process.env[SYNTH_CACHE_DIR_ENV];
        return;
    }
    process.env[SYNTH_CACHE_DIR_ENV] = fs.mkdtempSync(
        path.join(tmp, `${SYNTH_CACHE_DIR_PREFIX}${process.pid}-`)
    );
}
