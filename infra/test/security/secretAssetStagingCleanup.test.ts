/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A credential carried into a CDK code asset must not be left behind on the build host.
 *
 * Both secret populators (`populateHuggingFaceTokenSecret`, `populatePhysnaSecret`) write the value to
 * a `fs.mkdtempSync` directory and hand that directory to `lambda.Code.fromAsset()`. CDK stages the
 * contents into the cloud assembly while the construct is created, so once the Function exists the
 * source directory is a redundant cleartext copy — and `mkdtempSync` never removes it. On a machine
 * that had synthesized this app repeatedly there were 1,314 `vams-hf-token-*` and 724
 * `vams-physna-secret-*` directories, each holding the live credential; a CI runner keeps them for the
 * life of its workspace and `cdk.out` is commonly archived as a build artifact.
 *
 * The assertions come in pairs, because "the directory is gone" is satisfied just as well by a
 * populator that never created the asset at all:
 *
 *   1. No new temp directory survives the synth, AND
 *   2. the staged asset in the cloud assembly still contains the credential file, so the deployment
 *      still works. Without (2) this suite would pass on a construct that silently stopped
 *      populating the secret.
 *
 * The directory count is measured as a delta around the synth rather than as an absolute, because a
 * developer machine legitimately carries directories left by earlier runs of the old code and this
 * suite is not a cleanup tool.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Template } from "aws-cdk-lib/assertions";
import { populateHuggingFaceTokenSecret } from "../../lib/nestedStacks/pipelines/genAi/nvidia/customResources/populateHuggingFaceTokenSecret";
import { populatePhysnaSecret } from "../../lib/nestedStacks/addon/physna/customResources/populatePhysnaSecret";

/** Values chosen to be searchable in a staged file and obviously not real credentials. */
const HF_TOKEN = "hf_UNITTESTtokenVALUE0000000000000000";
const PHYSNA_CLIENT_ID = "unittest-physna-client-id";
const PHYSNA_CLIENT_SECRET = "unittest-physna-client-secret-0000000";

function tempDirs(prefix: string): Set<string> {
    const tmp = os.tmpdir();
    let entries: string[] = [];
    try {
        entries = fs.readdirSync(tmp);
    } catch {
        return new Set();
    }
    return new Set(entries.filter((e) => e.startsWith(prefix)).map((e) => path.join(tmp, e)));
}

/**
 * The staged asset directories in the cloud assembly that contain `fileName`.
 *
 * The asset's own directory name is a content hash, so it cannot be predicted from the input — the
 * assembly is scanned instead. Returning the file CONTENTS is what lets the caller assert the
 * credential really shipped rather than merely that some asset exists.
 */
function stagedAssetContents(outdir: string, fileName: string): string[] {
    const found: string[] = [];
    for (const entry of fs.readdirSync(outdir)) {
        if (!entry.startsWith("asset.")) continue;
        const candidate = path.join(outdir, entry, fileName);
        if (fs.existsSync(candidate)) {
            found.push(fs.readFileSync(candidate, { encoding: "utf-8" }));
        }
    }
    return found;
}

describe("secret code assets are not left on the build host", () => {
    const outdirs: string[] = [];

    afterAll(() => {
        // This suite's own assemblies, not the app's — removed so repeated runs do not accumulate the
        // very thing the suite exists to prevent.
        for (const dir of outdirs) {
            fs.rmSync(dir, { recursive: true, force: true });
        }
    });

    function synthWith(build: (stack: cdk.Stack) => void, assemblyPrefix: string) {
        const outdir = fs.mkdtempSync(path.join(os.tmpdir(), assemblyPrefix));
        outdirs.push(outdir);
        const app = new cdk.App({ outdir });
        const stack = new cdk.Stack(app, "SecretAssetTestStack", {
            env: { account: "123456789012", region: "us-west-2" },
        });
        build(stack);
        const template = Template.fromStack(stack);
        return { outdir, template, assemblyDir: app.synth().directory };
    }

    test("the HuggingFace populator removes its temp directory and still ships the token", () => {
        const before = tempDirs("vams-hf-token-");

        const { assemblyDir, template } = synthWith((stack) => {
            const secret = new secretsmanager.Secret(stack, "HfSecret");
            populateHuggingFaceTokenSecret(stack, "HfPopulate", secret, HF_TOKEN);
        }, "vams-hf-assembly-");

        const after = tempDirs("vams-hf-token-");
        const leaked = [...after].filter((d) => !before.has(d));
        expect(leaked).toEqual([]);

        // The staged copy must still carry the token, or the populator would deploy an empty secret.
        const staged = stagedAssetContents(assemblyDir, "token.json");
        expect(staged).toHaveLength(1);
        expect(JSON.parse(staged[0]).token).toBe(HF_TOKEN);

        // And the credential must still be absent from the template — the property this construct's
        // whole shape exists to provide.
        expect(JSON.stringify(template.toJSON())).not.toContain(HF_TOKEN);
    });

    test("the Physna populator removes its temp directory and still ships the credentials", () => {
        const before = tempDirs("vams-physna-secret-");

        const { assemblyDir, template } = synthWith((stack) => {
            const secret = new secretsmanager.Secret(stack, "PhysnaSecret");
            populatePhysnaSecret(
                stack,
                "PhysnaPopulate",
                secret,
                PHYSNA_CLIENT_ID,
                PHYSNA_CLIENT_SECRET
            );
        }, "vams-physna-assembly-");

        const after = tempDirs("vams-physna-secret-");
        const leaked = [...after].filter((d) => !before.has(d));
        expect(leaked).toEqual([]);

        const staged = stagedAssetContents(assemblyDir, "credentials.json");
        expect(staged).toHaveLength(1);
        const parsed = JSON.parse(staged[0]);
        expect(parsed.clientId).toBe(PHYSNA_CLIENT_ID);
        expect(parsed.clientSecret).toBe(PHYSNA_CLIENT_SECRET);

        const rendered = JSON.stringify(template.toJSON());
        expect(rendered).not.toContain(PHYSNA_CLIENT_SECRET);
    });

    test("a version property is present so a rotated credential re-runs the custom resource", () => {
        // Not decoration: a custom resource is re-invoked only by a property change, so without this
        // a rotation in config would leave the old value in Secrets Manager with no error. Asserted
        // as "differs between two different credentials" rather than against a fixed digest, so the
        // version scheme can change without a test edit.
        const build = (token: string) => {
            const outdir = fs.mkdtempSync(path.join(os.tmpdir(), "vams-hf-version-"));
            outdirs.push(outdir);
            const app = new cdk.App({ outdir });
            const stack = new cdk.Stack(app, "VersionStack", {
                env: { account: "123456789012", region: "us-west-2" },
            });
            const secret = new secretsmanager.Secret(stack, "HfSecret");
            populateHuggingFaceTokenSecret(stack, "HfPopulate", secret, token);
            const resources = Template.fromStack(stack).findResources(
                "AWS::CloudFormation::CustomResource"
            );
            const versions = Object.values(resources).map((r: any) => r.Properties?.tokenVersion);
            app.synth();
            return versions;
        };

        const a = build("hf_UNITTESTaaaaaaaaaaaaaaaaaaaaaaaaaa");
        const b = build("hf_UNITTESTbbbbbbbbbbbbbbbbbbbbbbbbbb");

        expect(a).toHaveLength(1);
        expect(a[0]).toBeTruthy();
        expect(a[0]).not.toEqual(b[0]);
    });
});
