/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The static-mode viewer key secret is filled by the add-on's own populator: the key travels in the
 * custom resource's code asset, not the template, the temp directory it was written to does not
 * outlive the synth, a changed key re-runs the resource, a delete keeps the physical id
 * CloudFormation holds, and with an imported KMS key the populator may encrypt under it.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as kms from "aws-cdk-lib/aws-kms";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { Template } from "aws-cdk-lib/assertions";
import { populateMirisViewerKeySecret } from "../../lib/nestedStacks/addon/miris/customResources/populateMirisViewerKeySecret";

const VIEWER_KEY = "unittest-miris-viewer-key-0000000000"; // pragma: allowlist secret
const KEY_ARN = "arn:aws:kms:us-west-2:123456789012:key/11111111-2222-3333-4444-555555555555";
const ASSET_PREFIX = "vams-miris-viewer-key-";
const BUILDER = path.resolve(
    __dirname,
    "../../lib/nestedStacks/addon/miris/mirisBuilder-nestedStack.ts"
);

function tempDirs(): Set<string> {
    return new Set(
        fs
            .readdirSync(os.tmpdir())
            .filter((e) => e.startsWith(ASSET_PREFIX))
            .map((e) => path.join(os.tmpdir(), e))
    );
}

/** The contents of `fileName` in every staged asset of the assembly that has one. */
function stagedFiles(assemblyDir: string, fileName: string): string[] {
    return fs
        .readdirSync(assemblyDir)
        .filter((e) => e.startsWith("asset."))
        .map((e) => path.join(assemblyDir, e, fileName))
        .filter((f) => fs.existsSync(f))
        .map((f) => fs.readFileSync(f, { encoding: "utf-8" }));
}

describe("the Miris viewer key secret populator", () => {
    const outdirs: string[] = [];
    afterAll(() => outdirs.forEach((d) => fs.rmSync(d, { recursive: true, force: true })));

    function synth(viewerKey: string, withKey = false) {
        const outdir = fs.mkdtempSync(path.join(os.tmpdir(), "vams-miris-populate-assembly-"));
        outdirs.push(outdir);
        const app = new cdk.App({ outdir });
        const stack = new cdk.Stack(app, "MirisPopulateStack", {
            env: { account: "123456789012", region: "us-west-2" },
        });
        const encryptionKey = withKey ? kms.Key.fromKeyArn(stack, "Key", KEY_ARN) : undefined;
        const secret = new secretsmanager.Secret(stack, "ViewerKeySecret", { encryptionKey });
        populateMirisViewerKeySecret(stack, "ViewerKeyPopulate", secret, viewerKey);
        const template = Template.fromStack(stack);
        return { template, assemblyDir: app.synth().directory };
    }

    test("ships the key in the code asset, not the template, and leaves no temp directory", () => {
        const before = tempDirs();
        const { template, assemblyDir } = synth(VIEWER_KEY);

        expect([...tempDirs()].filter((d) => !before.has(d))).toEqual([]);
        const staged = stagedFiles(assemblyDir, "viewerKey.json");
        expect(staged).toHaveLength(1);
        expect(JSON.parse(staged[0]).viewerKey).toBe(VIEWER_KEY);
        expect(JSON.stringify(template.toJSON())).not.toContain(VIEWER_KEY);
    });

    test("a changed key changes the resource's version property, so the secret is rewritten", () => {
        const version = (key: string) =>
            Object.values(
                synth(key).template.findResources("AWS::CloudFormation::CustomResource")
            ).map((r: any) => r.Properties.viewerKeyVersion);

        const [a] = version("unittest-miris-viewer-key-aaaaaaaa");
        const [b] = version("unittest-miris-viewer-key-bbbbbbbb");
        expect(a).toMatch(/^[0-9a-f]{64}$/);
        expect(a).not.toEqual(b);
    });

    test("the handler echoes the physical id CloudFormation holds and mints a Miris one", () => {
        const [handler] = stagedFiles(synth(VIEWER_KEY).assemblyDir, "index.py");
        expect(handler).toContain('event.get("PhysicalResourceId")');
        expect(handler).toContain('"MirisViewerKeySecretPopulate:"');
    });

    test("with an imported KMS key the populator may encrypt under it", () => {
        const policies = synth(VIEWER_KEY, true).template.findResources("AWS::IAM::Policy");
        const kmsStatements = Object.values(policies)
            .flatMap((p: any) => p.Properties.PolicyDocument.Statement)
            .filter((s: any) => [s.Action].flat().includes("kms:GenerateDataKey*"));
        expect(kmsStatements.map((s: any) => s.Resource)).toEqual([KEY_ARN]);
    });

    test("the add-on builder uses its own populator, not a pipeline's", () => {
        const source = fs.readFileSync(BUILDER, { encoding: "utf-8" });
        expect(source).toContain('from "./customResources/populateMirisViewerKeySecret"');
        expect(source).not.toMatch(/from "[^"]*\/pipelines\//);
    });
});
