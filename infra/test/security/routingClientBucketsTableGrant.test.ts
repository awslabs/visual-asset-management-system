/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every Lambda whose handler holds a Region-routing S3 client can read the asset buckets table.
 *
 * `backend/backend/common/s3.py`'s routing client signs each call for the Region of the bucket it
 * names, and resolves a name it has not seen through the buckets table's `bucketNameGSI`
 * (`dynamodb:Query`). A handler whose Lambda role lacks that grant does not fail loudly: the lookup is
 * denied, the miss is cached and the call is routed to the deployment Region — which inside the VPC
 * sends a cross-Region copy down the `CopyObject` path an Amazon S3 interface endpoint does not serve.
 *
 * The set of handler modules is read from the backend ratchet that holds those modules at zero plain
 * S3 clients (`backend/tests/handlers/databases/test_buckets_listing_region.py`, `ASSET_BUCKET_HANDLERS`),
 * so a handler added to that list is checked here without anyone editing this file, and a handler
 * missing from both is a ratchet gap, not a passing test. The synthesized assembly is scanned for every
 * `AWS::Lambda::Function` whose `Handler` names one of those modules — across every nested stack,
 * whatever builder created it — and its role's policies (inline, attached and managed) must carry a
 * `dynamodb:Query` statement on the `S3AssetBucketsStorageTable`. The add-on handlers exist only when
 * their add-ons are enabled, so the assembly is synthesized with both add-ons on.
 */

import * as fs from "fs";
import * as path from "path";
import { synthTemplate, SynthResult, Resource } from "../support/templateSynth";

const RATCHET = path.join(
    __dirname,
    "..",
    "..",
    "..",
    "backend",
    "tests",
    "handlers",
    "databases",
    "test_buckets_listing_region.py"
);

/** The handler modules the backend ratchet lists, by module basename (`uploadFile`, ...). */
function routingHandlerModules(): string[] {
    const source = fs.readFileSync(RATCHET, "utf8");
    const start = source.indexOf("ASSET_BUCKET_HANDLERS = [");
    const end = source.indexOf("]", start);
    expect(start).toBeGreaterThan(0);
    const modules = [...source.slice(start, end).matchAll(/"handlers\/([A-Za-z0-9_/]+)\.py"/g)].map(
        (m) => m[1].split("/").pop() as string
    );
    expect(modules.length).toBeGreaterThan(20);
    return modules;
}

/**
 * Modules in the list that are imported by other handlers rather than deployed as a Lambda of their
 * own: no `Handler` names them, so there is nothing to assert for them here.
 */
const LIBRARY_MODULES = new Set(["physnaCommon"]);

const synth = (): SynthResult =>
    synthTemplate("commercial", {
        mutateKey: "routing-client-grants-addons-on",
        mutate: (c) => {
            c.app.addons.usePhysnaSync.enabled = true;
            c.app.addons.usePhysnaSync.tenantId = "11111111-2222-3333-4444-555555555555";
            c.app.addons.useGarnetFramework.enabled = true;
            c.app.addons.useGarnetFramework.garnetApiEndpoint = "https://garnet.example.com";
            c.app.addons.useGarnetFramework.garnetApiToken = "token";
            c.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl =
                "https://sqs.us-east-1.amazonaws.com/123456789012/garnet-ingest";
        },
    });

/** `handlers.assets.uploadFile.lambda_handler` -> `uploadFile`. */
function handlerModule(handler: unknown): string {
    const parts = String(handler ?? "").split(".");
    return parts.length >= 2 ? parts[parts.length - 2] : "";
}

/** Every IAM statement attached to a role, from the three places CDK can put one. */
function statementsForRole(s: SynthResult, stack: string, roleLogicalId: string): any[] {
    const out: any[] = [];
    const inStack = s.resources.filter((r) => r.stack === stack);
    for (const r of inStack) {
        if (
            r.type === "AWS::IAM::Policy" &&
            JSON.stringify(r.properties.Roles).includes(roleLogicalId)
        ) {
            out.push(...(r.properties.PolicyDocument?.Statement ?? []));
        }
        if (
            r.type === "AWS::IAM::ManagedPolicy" &&
            JSON.stringify(r.properties.Roles ?? []).includes(roleLogicalId)
        ) {
            out.push(...(r.properties.PolicyDocument?.Statement ?? []));
        }
        if (r.type === "AWS::IAM::Role" && r.logicalId === roleLogicalId) {
            for (const p of r.properties.Policies ?? [])
                out.push(...(p.PolicyDocument?.Statement ?? []));
        }
    }
    return out;
}

function grantsBucketsTableRead(statements: any[]): boolean {
    return statements.some((st) => {
        const actions = JSON.stringify(st.Action ?? "");
        const resources = JSON.stringify(st.Resource ?? "");
        return (
            st.Effect === "Allow" &&
            /dynamodb:Query/.test(actions) &&
            /S3AssetBucketsStorage/.test(resources)
        );
    });
}

describe("every Region-routing S3 handler Lambda can read the asset buckets table", () => {
    const modules = routingHandlerModules();
    const s = synth();
    const lambdas = s
        .ofType("AWS::Lambda::Function")
        .filter((r) => modules.includes(handlerModule(r.properties.Handler)));

    test("the assembly carries a Lambda for every deployable module in the ratchet", () => {
        // Positive control: a module whose Lambda the synth does not emit would pass the grant
        // assertion vacuously. Library modules are excluded by name, deliberately.
        const emitted = new Set(lambdas.map((r) => handlerModule(r.properties.Handler)));
        const missing = modules.filter((m) => !LIBRARY_MODULES.has(m) && !emitted.has(m));
        expect(missing).toEqual([]);
    });

    test("every such Lambda's role grants dynamodb:Query on the buckets table", () => {
        const ungranted = lambdas
            .filter((r: Resource) => {
                const role = r.properties.Role?.["Fn::GetAtt"]?.[0];
                return !grantsBucketsTableRead(statementsForRole(s, r.stack, String(role)));
            })
            .map((r) => `${r.stack}/${r.logicalId} (${r.properties.Handler})`);
        expect(ungranted).toEqual([]);
    });

    test("the grant check can see a grant (control)", () => {
        const [uploadFile] = lambdas.filter(
            (r) => handlerModule(r.properties.Handler) === "uploadFile"
        );
        expect(uploadFile).toBeDefined();
        const role = uploadFile.properties.Role["Fn::GetAtt"][0];
        expect(grantsBucketsTableRead(statementsForRole(s, uploadFile.stack, role))).toBe(true);
        expect(
            grantsBucketsTableRead([{ Effect: "Allow", Action: ["s3:GetObject"], Resource: "*" }])
        ).toBe(false);
    });
});
