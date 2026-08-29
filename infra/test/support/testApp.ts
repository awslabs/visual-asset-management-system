/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A `cdk.App` for tests whose cloud assembly is deleted when the test file finishes.
 *
 * Why this exists. `new cdk.App()` with no `outdir` sends its assembly to a FRESH temporary directory
 * (`fs.mkdtempSync(os.tmpdir() + "/cdk.out")`) and nothing removes it: CDK registers the directory for
 * cleanup on the Node `exit` event, and Jest never fires that event
 * (<https://github.com/jestjs/jest/issues/10927>). The directory is created eagerly by the constructor,
 * so both shapes leak:
 *
 *   - an app built only to read configuration (`Config.getConfig(new cdk.App())`) leaves an EMPTY
 *     directory behind;
 *   - an app that reaches `Template.fromStack()` or `app.synth()` leaves the whole assembly behind,
 *     which for a full VAMS synth is ~280 MB because every Lambda asset is staged into it.
 *
 * Measured on this repository before the conversion: 237 orphaned `cdk.out*` directories in the temp
 * directory totalling 9.3 GB, roughly a third of them the empty configuration-only shape.
 *
 * ## Use it instead of `new cdk.App()`
 *
 * ```ts
 * import { newTestApp } from "./support/testApp";
 *
 * const app = newTestApp();                       // instead of new cdk.App()
 * const app = newTestApp({ context: { ... } });   // AppProps are passed straight through
 * ```
 *
 * The returned app is an ordinary `cdk.App`; the only difference is that its `outdir` is a temporary
 * directory this module owns and removes afterwards, so no assertion behaves differently.
 *
 * ## Cleanup is registered on import
 *
 * Importing this module registers one `afterAll` hook, which is how `aws-cdk-lib`'s own
 * `testhelpers/jest-autoclean` module does it. That keeps the conversion to a single call-site change
 * per app and means a test cannot opt into the helper and still forget the teardown. The hook also calls
 * CDK's own `CloudAssembly.cleanupTemporaryDirectories()`, so an app that some other module in the same
 * test file created with no `outdir` is cleaned up as well.
 *
 * Set `VAMS_KEEP_TEST_ASSEMBLIES=1` to keep the assemblies for inspection — and to measure the leak the
 * conversion removes, since it turns the cleanup off without reverting the conversion.
 *
 * ## What holds the line for a file that does not use this helper
 *
 * `jest.config.js` registers aws-cdk-lib's `testhelpers/jest-autoclean` hook, which sweeps CDK's own
 * temporary assembly directories at the end of every test file, so a missed call site leaks nothing
 * lasting (`CDK_NO_CLEAN_TESTS=1` turns that hook off, which is how the leak can be measured).
 * `test/support/harnessGuards.test.ts` is what FAILS on such a file; the hook only cleans up after it.
 */

import * as cdk from "aws-cdk-lib";
import * as cxapi from "aws-cdk-lib/cx-api";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

/** Outdirs handed out by `newTestApp`, in creation order. */
const assemblyDirs: string[] = [];

/**
 * A `cdk.App` whose assembly lands in a temporary directory that is removed after the test file.
 *
 * An explicit `outdir` in `props` is honored and left alone: a caller that names its own directory owns
 * its lifetime.
 */
export function newTestApp(props: cdk.AppProps = {}): cdk.App {
    if (props.outdir) return new cdk.App(props);

    const outdir = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), "vams-cdk-test"));
    assemblyDirs.push(outdir);
    return new cdk.App({ ...props, outdir });
}

/**
 * Remove every assembly directory created by `newTestApp`, plus any CDK made for itself.
 *
 * Best-effort by design: a failed unlink must not fail a test that already produced a valid result, and
 * on Windows an antivirus scanner or the search indexer can hold a handle open for a moment.
 */
export function removeTestAppAssemblies(): void {
    if (process.env.VAMS_KEEP_TEST_ASSEMBLIES) return;

    for (const dir of assemblyDirs.splice(0)) {
        try {
            fs.rmSync(dir, { recursive: true, force: true });
        } catch {
            /* leaves a temp directory behind; never a test failure */
        }
    }

    try {
        cxapi.CloudAssembly.cleanupTemporaryDirectories();
    } catch {
        /* same: cleanup is not an assertion */
    }
}

if (typeof afterAll === "function") {
    afterAll(removeTestAppAssemblies);
}
