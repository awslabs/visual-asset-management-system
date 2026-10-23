/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Instance user data must be base64-encoded by CloudFormation, not by the synth process.
 *
 * The GPU pipelines build their user data as a template string carrying CDK tokens -- the EFS file
 * system id of the model cache, the model cache bucket name, the region. Encoding that string with
 * Buffer.from(...).toString("base64") freezes each token as its DEBUG TEXT, because a base64 blob is
 * opaque to the token resolver that runs afterwards. The deployed launch template then reads:
 *
 *     mount -t efs -o tls ${Token[TOKEN.6935]}:/ /mnt/efs/imageto3d-models
 *
 * which is not a file system id, so the mount silently fails on every instance. Measured on the Image
 * to 3D pipeline: the filesystem recorded no client connection at all across a day of GPU jobs, and
 * every run restored its whole model cache from Amazon S3 instead -- 15 seconds for the smallest
 * model's weights and 461 for the largest, on billed GPU time. Nothing failed; the runs were simply
 * slower than they looked, and the "EFS cache with an S3 backup" was an S3 cache all along.
 *
 * Fn.base64 emits Fn::Base64, so the encoding happens after token resolution.
 *
 * This test reads the SOURCE rather than a synthesized template, deliberately: a synth-based check
 * would only cover the pipelines the test app happens to enable, and this defect is invisible in a
 * disabled pipeline until someone turns it on.
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

/** The token-bearing interpolations that make in-process encoding wrong. */
const TOKEN_INTERPOLATIONS = [/\$\{[^}]*fileSystemId\}/, /\$\{[^}]*bucketName\}/, /\$\{region\}/];

describe("launch template user data", () => {
    const sources = pipelineSources(PIPELINES_DIR);

    test("the pipelines tree is being read", () => {
        expect(sources.length).toBeGreaterThan(5);
    });

    test.each(sources.map((file) => [path.relative(PIPELINES_DIR, file), file]))(
        "%s encodes token-bearing user data through CloudFormation",
        (_relative, file) => {
            const source = fs.readFileSync(file, "utf-8");
            if (!source.includes("userData")) {
                return;
            }

            const encodesInProcess = source.includes('Buffer.from(userData).toString("base64")');
            const carriesTokens = TOKEN_INTERPOLATIONS.some((pattern) => pattern.test(source));

            // In-process encoding is only safe for user data with no tokens in it at all.
            expect(encodesInProcess && carriesTokens).toBe(false);
        }
    );

    test.each(sources.map((file) => [path.relative(PIPELINES_DIR, file), file]))(
        "%s pins the launch template version so a user-data edit reaches an instance",
        (_relative, file) => {
            const source = fs.readFileSync(file, "utf-8");
            // Scoped to the L1 pattern, which is where the defect lives. The Isaac Lab construct
            // passes an L2 launch template to an L2 compute environment, which exposes no version to
            // set -- it takes the template's DEFAULT version, which is the same staleness and has no
            // fix at this layer. Its user data carries no tokens, so nothing there is silently wrong.
            if (!source.includes("launchTemplateId:")) {
                return;
            }

            // Required only where the user data carries tokens -- the only case where a stale merge is
            // WRONG rather than merely old. Where it carries none, "$Latest" costs nothing, and
            // pinning can be actively unavailable: the shared batch-gpu-pipeline construct's
            // environment carries an explicit name, so CloudFormation refuses to replace it ("cannot
            // update a stack when a custom-named resource requires replacing"), and Batch refuses the
            // in-place alternative because that environment passes an explicit serviceRole rather than
            // using the service-linked role ("Following fields ... launchTemplate ... can be updated
            // only for ... Compute Environment having a Batch Service Linked Role"). Both were tried
            // against the live stack, and each rolled back nine pipeline stacks.
            const carriesTokens = TOKEN_INTERPOLATIONS.some((pattern) => pattern.test(source));
            if (!carriesTokens) {
                return;
            }

            // AWS Batch does not read the launch template when an instance launches -- it merges it
            // with its own bootstrap into a Batch-managed copy when the compute environment is created
            // or updated. "$Latest" is a constant, so a new template version is not a change to the
            // compute environment: CloudFormation updates nothing and Batch goes on handing instances
            // the stale merge. Measured on the Image to 3D pipeline: the pipeline's launch template
            // had reached a later version carrying a correct EFS mount command, while the instance that
            // ran the job booted Batch's merged copy of version 1, still carrying the frozen token. A
            // fix to the template alone reached no instance.
            expect(source).not.toContain('version: "$Latest"');
            expect(source).toContain("attrLatestVersionNumber");
        }
    );

    test.each(sources.map((file) => [path.relative(PIPELINES_DIR, file), file]))(
        "%s updates its compute environment in place",
        (_relative, file) => {
            const source = fs.readFileSync(file, "utf-8");
            if (!source.includes("launchTemplateId:")) {
                return;
            }

            // These three together are what CloudFormation requires for an in-place infrastructure
            // update, and all three must hold or a user-data edit cannot reach a running fleet by ANY
            // route: no serviceRole (so the Batch service-linked role is used), a progressive allocation
            // strategy, and replaceComputeEnvironment: false. With a named service role Batch refuses the
            // in-place update; with replaceComputeEnvironment left at its default the alternative is a
            // full environment replacement, which re-points job queues and leaves orphans behind.
            expect(source).not.toContain("serviceRole:");
            expect(source).toContain('allocationStrategy: "BEST_FIT_PROGRESSIVE"');
            expect(source).toContain("replaceComputeEnvironment: false");

            // replaceComputeEnvironment: false is only safe to ship to an EXISTING deployment because
            // the environment's construct id changed in the same release: that makes the upgrade a
            // create-and-delete rather than an update, so this property never has to permit the one
            // replacement the upgrade needs. An environment still naming a service role can be neither
            // updated in place nor migrated, so without the rename a v2.5 upgrade fails outright --
            // measured, twice, against a live stack. The ids must therefore not drift back.
            expect(source).not.toMatch(/["`]\w*OnDemandComputeEnv/);
        }
    );

    test.each(sources.map((file) => [path.relative(PIPELINES_DIR, file), file]))(
        "%s writes each user-data command on one line",
        (_relative, file) => {
            const source = fs.readFileSync(file, "utf-8");
            const match = /const userData = `([\s\S]*?)\n`;/.exec(source);
            if (!match) {
                return;
            }
            const script = match[1];

            // Inside a template literal, backslash-newline is a JavaScript LINE CONTINUATION: both
            // characters are removed before the string exists, so a shell continuation cannot be
            // written this way. Most collapsed harmlessly into valid one-liners, but the one written
            // "\n" was a newline ESCAPE, which split `aws s3 cp` across two lines and left it invalid
            // -- which is why the mount log never reached S3 and the failure had to be read off the
            // instance's serial console instead.
            const continuations = script
                .split("\n")
                .filter((line) => line.endsWith("\\"))
                .map((line) => line.trim());
            expect(continuations).toEqual([]);
            expect(script).not.toContain("\\n");
        }
    );

    test("no construct writes a token's debug text into user data", () => {
        // Comments are stripped first: the constructs that carried this defect now document it by
        // quoting the frozen text, and a guard that forbids describing a bug is a guard that gets
        // deleted rather than one that gets respected.
        for (const file of sources) {
            const code = fs
                .readFileSync(file, "utf-8")
                .replace(/\/\*[\s\S]*?\*\//g, "")
                .replace(/^\s*\/\/.*$/gm, "");
            expect(code).not.toContain("${Token[");
        }
    });
});
