/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Instance user data must be base64-encoded by CloudFormation, not by the synth process.
 *
 * The GPU pipelines build their user data as a template string carrying CDK tokens — the EFS file
 * system id of the model cache among them. Encoding that string with
 * `Buffer.from(...).toString("base64")` freezes each token as its DEBUG TEXT, because a base64 blob is
 * opaque to the token resolver that runs afterwards. The deployed launch template then read:
 *
 *     mount -t efs -o tls ${Token[TOKEN.6935]}:/ /mnt/efs/cosmos-models
 *
 * Bash parses `${Token[...]}` as an array subscript, reports "invalid arithmetic operator", and aborts
 * the whole scripts-user module — so every later line was skipped too. The shared model cache was never
 * mounted: nothing failed, and every run restored its weights from Amazon S3 onto local disk and
 * uploaded them again afterwards, on billed GPU time. `Fn.base64` emits `Fn::Base64`, so the encoding
 * happens after token resolution.
 *
 * This reads the SOURCE rather than a synthesized template, deliberately: a synth-based check would
 * only cover the pipelines the test app happens to enable, and this defect is invisible in a disabled
 * pipeline until someone turns it on.
 *
 * The variable-name generality below is the point. An earlier form of this guard matched the literal
 * string `Buffer.from(userData).toString("base64")`, which silently exempted every launch template whose
 * script was held in a differently-named variable — `userDataSuper` in Cosmos 3 and `userData14B` in
 * Cosmos Predict, both of which interpolate the same EFS file system id. The guard passed while two
 * compute environments still mounted nothing.
 */

import * as fs from "fs";
import * as path from "path";

const PIPELINES_DIR = path.join(__dirname, "..", "lib", "nestedStacks", "pipelines");

/** Every .ts file under the pipelines tree. */
function pipelineSources(dir: string): string[] {
    const found: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            found.push(...pipelineSources(full));
        } else if (entry.name.endsWith(".ts")) {
            found.push(full);
        }
    }
    return found;
}

/** Source with comments removed, so a comment describing the defect is not read as the defect. */
function code(file: string): string {
    return fs
        .readFileSync(file, "utf-8")
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/^\s*\/\/.*$/gm, "");
}

/** The token-bearing interpolations that make in-process encoding wrong. */
const TOKEN_INTERPOLATIONS = [/\$\{[^}]*fileSystemId\}/, /\$\{[^}]*bucketName\}/, /\$\{region\}/];

/** Whether this source builds any user data from a token-bearing string. */
function carriesTokens(source: string): boolean {
    return TOKEN_INTERPOLATIONS.some((pattern) => pattern.test(source));
}

describe("launch template user data", () => {
    const sources = pipelineSources(PIPELINES_DIR);

    test("the pipelines tree is being read", () => {
        // Without this the suite passes by reading nothing.
        expect(sources.length).toBeGreaterThan(5);
    });

    test("at least one construct is token-bearing, so the guards below have something to check", () => {
        // The whole file set could stop matching TOKEN_INTERPOLATIONS after a refactor — renaming
        // `fileSystemId`, say — and every per-file assertion would then skip and the suite stay green.
        expect(sources.filter((f) => carriesTokens(code(f))).length).toBeGreaterThan(0);
    });

    test.each(sources.map((file) => [path.relative(PIPELINES_DIR, file), file]))(
        "%s encodes token-bearing user data through CloudFormation",
        (_relative, file) => {
            const source = code(file);
            if (!source.includes("userData")) return;

            // ANY variable name, not just `userData`. Matching one name is what let two broken sites
            // through: the check has to be about the operation, not about what the string is called.
            const inProcess = [
                ...source.matchAll(/Buffer\.from\((\w+)\)\.toString\("base64"\)/g),
            ].map((m) => m[1]);
            if (!carriesTokens(source)) return;

            expect(inProcess).toEqual([]);
        }
    );

    test.each(sources.map((file) => [path.relative(PIPELINES_DIR, file), file]))(
        "%s pins the launch template version so a user-data edit reaches an instance",
        (_relative, file) => {
            const source = code(file);
            // Scoped to the L1 pattern, which is where the defect lives. The Isaac Lab construct passes
            // an L2 launch template to an L2 compute environment, which exposes no version to set.
            if (!source.includes("launchTemplateId:")) return;

            // Required only where the user data carries tokens — the only case where a stale merge is
            // WRONG rather than merely old. The shared batch-gpu-pipeline construct interpolates none,
            // and its compute environment carries an explicit name, which CloudFormation refuses to
            // replace; pinning it would break a deployment to fix nothing.
            if (!carriesTokens(source)) return;

            // AWS Batch does not read the launch template when an instance launches — it merges it with
            // its own bootstrap into a Batch-managed copy when the compute environment is created or
            // updated. "$Latest" is a constant, so a new template version is not a change to the
            // environment: CloudFormation updates nothing and Batch goes on handing instances the stale
            // merge. This is why the encoding fix alone reached no instance.
            expect(source).not.toContain('version: "$Latest"');
            expect(source).toContain("attrLatestVersionNumber");
        }
    );

    test.each(sources.map((file) => [path.relative(PIPELINES_DIR, file), file]))(
        "%s can deliver a user-data change to its compute environment",
        (_relative, file) => {
            const source = code(file);
            if (!source.includes("launchTemplateId:") || !carriesTokens(source)) return;

            // Naming a service role forbids an in-place update of the launch template, instance types,
            // subnets or security groups: Batch permits those "only for ... Compute Environment having a
            // Batch Service Linked Role". With a named role and a named environment, a change to the
            // start-up script cannot reach a running fleet by ANY route — not in place, and not by
            // replacement.
            expect(source).not.toContain("serviceRole:");
            expect(source).toContain('allocationStrategy: "BEST_FIT_PROGRESSIVE"');
            expect(source).toContain("replaceComputeEnvironment: false");
            expect(source).toContain("updateToLatestImageVersion: true");

            // `replaceComputeEnvironment: false` is only safe to ship to an EXISTING deployment because
            // each environment's construct id changed in the same release: that makes the upgrade a
            // create-and-delete rather than an update, so the property never has to permit the one
            // replacement the upgrade needs. An environment still naming a service role can be neither
            // updated in place nor migrated to the service-linked role. The ids must not drift back.
            expect(source).not.toMatch(/["'`]\w*OnDemandComputeEnv/);
        }
    );

    test.each(sources.map((file) => [path.relative(PIPELINES_DIR, file), file]))(
        "%s writes each user-data command on one line",
        (_relative, file) => {
            const source = fs.readFileSync(file, "utf-8");
            // Every user-data string, whatever it is called — same generality as the encoding guard.
            const scripts = [...source.matchAll(/const userData\w* = `([\s\S]*?)\n`;/g)].map(
                (m) => m[1]
            );
            if (!scripts.length) return;

            for (const script of scripts) {
                // Inside a template literal, backslash-newline is a JavaScript LINE CONTINUATION: both
                // characters are removed before the string exists, so a shell continuation cannot be
                // written this way. Most collapse into valid one-liners, but a written "\n" is a newline
                // ESCAPE, which splits a command across two lines and leaves it invalid — which is how
                // the mount diagnostic never reached S3.
                const continuations = script
                    .split("\n")
                    .filter((line) => line.endsWith("\\"))
                    .map((line) => line.trim());
                expect(continuations).toEqual([]);
                expect(script).not.toContain("\\n");
            }
        }
    );

    test("no construct writes a token's debug text into user data", () => {
        // Comments are stripped first: the constructs that carried this defect now document it by
        // quoting the frozen text, and a guard that forbids describing a bug is one that gets deleted
        // rather than respected.
        for (const file of sources) {
            expect(code(file)).not.toContain("${Token[");
        }
    });
});
