/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The two settings that keep the synth-tier suite stable under parallel workers, pinned so neither can
 * be dropped by a refactor without a test going red.
 *
 * `newTestApp()` disables asset staging. Staging copies ~280 MB of Lambda code and layer zips into the
 * assembly per full synth, and no template assertion reads any of it: hashes are computed from the
 * source, and a jest-driven synth emits no `aws:asset:path` metadata (that needs
 * `aws:cdk:enable-asset-metadata`, which only the CDK CLI injects). Measured with two synths of the
 * same template, one with staging on and one off: every differing leaf was a timestamp or a random
 * name that also differs between two synths with the SAME setting -- zero leaves changed because of
 * the flag. What the copy did do was fail under load (`UNKNOWN: unknown error, copyfile` on a layer zip
 * with 44 synth files across 31 workers) and make per-file teardown slow enough for jest to force-exit
 * a worker.
 *
 * `jest.config.js` bounds `maxWorkers`. Jest's default is one worker per core minus one, so a 32-core
 * host ran 31 concurrent full synths; one file measured at 66 s alone took 768 s in such a run, and
 * whether the run passed depended on the machine it ran on. These are durable guards, not temporary
 * ones (root CLAUDE.md Rule 13): both settings are one-line deletions that would silently bring the
 * flakiness back.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as cxapi from "aws-cdk-lib/cx-api";
import { newTestApp } from "./testApp";

describe("newTestApp disables asset staging", () => {
    test("by default", () => {
        const app = newTestApp();
        expect(app.node.tryGetContext(cxapi.DISABLE_ASSET_STAGING_CONTEXT)).toBe(true);
        // The other half of the Docker-free technique must still be there too.
        expect(app.node.tryGetContext(cxapi.BUNDLING_STACKS)).toEqual([]);
    });

    test("a caller that needs the staged copy can turn it back on", () => {
        // The caller's context is spread last, so it wins. Without this, the one suite that asserts on
        // a staged asset would have to bypass the harness -- and the harness guard forbids that.
        const app = newTestApp({ context: { [cxapi.DISABLE_ASSET_STAGING_CONTEXT]: false } });
        expect(app.node.tryGetContext(cxapi.DISABLE_ASSET_STAGING_CONTEXT)).toBe(false);
    });

    test("the flag actually stops the copy: a file asset is not staged into the outdir", () => {
        // Behavioural, not declarative -- the context key could be set and ignored by a future CDK.
        // A one-file asset under the default app must leave NOTHING under the assembly's asset dir,
        // while the same asset under a staging-enabled app must produce an `asset.<hash>` entry.
        const src = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), "vams-stage-probe"));
        fs.writeFileSync(path.join(src, "payload.txt"), "probe", "utf8");
        try {
            const staged = (app: cdk.App) => {
                const stack = new cdk.Stack(app, "S");
                new cdk.aws_s3_assets.Asset(stack, "A", { path: src });
                const asm = app.synth();
                return fs.readdirSync(asm.directory).filter((f) => f.startsWith("asset."));
            };
            expect(staged(newTestApp())).toEqual([]);
            expect(
                staged(newTestApp({ context: { [cxapi.DISABLE_ASSET_STAGING_CONTEXT]: false } }))
                    .length
            ).toBe(1);
        } finally {
            fs.rmSync(src, { recursive: true, force: true });
        }
    });
});

describe("jest.config.js bounds the worker pool", () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const config = require("../../jest.config.js");

    test("maxWorkers is set, and is a small number that does not scale with the core count", () => {
        // A percentage or an unset value follows the CORE count, which is the failure mode: the bound
        // exists precisely so a many-core machine does not run one full synth per core. Scaling by
        // memory is fine (that is the resource a synth worker actually consumes) as long as it is capped.
        expect(typeof config.maxWorkers).toBe("number");
        expect(config.maxWorkers).toBeGreaterThanOrEqual(1);
        // Four is where the full suite was fastest (2 -> 695 s, 4 -> 577 s, 8 -> 822 s).
        expect(config.maxWorkers).toBeLessThanOrEqual(4);
    });

    test("swollen workers are recycled between files", () => {
        expect(config.workerIdleMemoryLimit).toBeDefined();
    });
});
