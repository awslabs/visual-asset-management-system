/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: IAM scoping and service-helper usage, asserted against the CloudFormation each shipped config
 * template actually emits.
 *
 * Three fixes are covered, and each one is asserted in its POST-fix state:
 *
 *  - FIX-024 (S3-CONTRACTS-018) — CLOSED. Three Lambdas held an unscoped `apigateway.amazonaws.com`
 *    invoke permission (no `SourceArn`) while every route Lambda got a scoped one, so any REST API in
 *    any account could invoke them. All three `grantInvoke(Service("APIGATEWAY").Principal)` calls are
 *    gone and the assertions run as ordinary passing tests. The accompanying tests pin the two
 *    preconditions that make the deletion safe rather than an outage: the anonymous bootstrap Lambdas
 *    hold a SCOPED permission from the REST API builder's registry loop, and BOTH OpenAPI security
 *    schemes set `authorizerCredentials` so API Gateway reaches the authorizer through a role.
 *
 *  - FIX-031 (S1-INFRA-041) — `buildAssetLinksMetadataFunction` pointed at a backend module that does
 *    not exist and has been deleted. It was never called, so nothing was ever emitted and the synth
 *    assertion passed both before and after; it is kept as a regression guard, generalised to "every
 *    emitted `handlers.*` Lambda resolves to a real module". The assertion that actually caught the dead
 *    builder is source-level and lives in `lambdaBuilderHandlerModules.test.ts`, because a builder with
 *    no call site emits no CloudFormation for a template assertion to look at.
 *
 *  - FIX-015 (S2-BACKEND-024) — each Physna sync queue redrives to its own DLQ, and both event source
 *    mappings emit `FunctionResponseTypes: ["ReportBatchItemFailures"]`. Without that property Lambda
 *    ignores the handlers' `batchItemFailures` and deletes the whole batch on a 200, so the assertions
 *    below read the emitted template rather than the construct call. No shipped template enables Physna,
 *    so every assertion here runs against a HYBRID config (`mutate` + `mutateKey`) with
 *    `app.addons.usePhysnaSync.enabled = true`. That is the only way to see these resources at all: an
 *    assertion written against a shipped template finds nothing and passes vacuously.
 *
 * All three fixes have landed, so no test here is an `it.failing` ratchet any more; each one asserts the
 * post-fix state and must pass. Every negative assertion still keeps its positive control in its own
 * ordinary test rather than inside the negative: `expectAbsent()` throws when its control finds nothing,
 * and under `.failing` that throw would have SATISFIED the ratchet — the one way the suite could have
 * reported green while asserting nothing. Keep the separation if a future finding reintroduces a ratchet.
 */

import * as fs from "fs";
import * as path from "path";
import {
    ALL_TEMPLATES,
    SynthResult,
    TemplateName,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";

// A full-app synth is ~20 s, and this file needs five of them (three shipped templates + two hybrids).
jest.setTimeout(600_000);

const synth = (name: TemplateName): SynthResult => synthTemplate(name);

/**
 * Physna hybrid. The shipped templates all set `usePhysnaSync.enabled: false`, so the add-on's queues,
 * event source mappings and lambdas are NEVER emitted from a shipped config. `tenantId` is the one field
 * the templates leave blank that `getConfig()` requires (a UUID); the endpoints already carry valid
 * values, so nothing else needs filling in.
 */
const PHYSNA_TENANT_ID = "11111111-2222-3333-4444-555555555555";
const enablePhysna = (c: any) => {
    c.app.addons.usePhysnaSync.enabled = true;
    c.app.addons.usePhysnaSync.tenantId = PHYSNA_TENANT_ID;
};
const physnaSynth = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enablePhysna, mutateKey: "physnaSyncEnabled" });

/* ------------------------------------------------------------------------------------------------ *
 * FIX-024 — API Gateway invoke permissions must be scoped to this REST API
 * ------------------------------------------------------------------------------------------------ */

/** Every `AWS::Lambda::Permission` whose principal is the partition's API Gateway service principal. */
const apiGatewayPermissions = (s: SynthResult): typeof s.resources =>
    s.where("AWS::Lambda::Permission", (r) =>
        /^apigateway\./.test(SynthResult.flatten(r.properties.Principal))
    );

const scopedPermissions = (s: SynthResult) =>
    apiGatewayPermissions(s).filter((r) => r.properties.SourceArn !== undefined);

const unscopedPermissions = (s: SynthResult) =>
    apiGatewayPermissions(s).filter((r) => r.properties.SourceArn === undefined);

describe("FIX-024 API Gateway invoke permissions are scoped to the REST API", () => {
    /**
     * All three unscoped grants are gone. They were the REST authorizer (`authFunctions.ts`), the
     * amplify-config Lambda (`amplify-config-lambda-construct.ts`) and the version Lambda
     * (`vams-version-lambda-construct.ts`); each emitted an `AWS::Lambda::Permission` with
     * `Principal: apigateway.amazonaws.com` and NO `SourceArn`, which any API Gateway REST API in any
     * account satisfies.
     *
     * Every remaining permission comes from the registry loop in `rest-api-gateway-construct.ts`, which
     * scopes each one to `arn:<partition>:execute-api:<region>:<account>:<thisApi>/*` — including the two
     * anonymous bootstrap Lambdas, because they are registered as routes like any other. The authorizer
     * needs no resource policy at all: API Gateway assumes `RestAuthorizerInvokeRole`, named as
     * `authorizerCredentials` on both security schemes.
     */
    for (const name of ALL_TEMPLATES) {
        it(`FIX-024: ${name} emits no unscoped apigateway invoke permission`, () => {
            const s = synth(name);
            const found = unscopedPermissions(s).map((r) => `${r.stack}/${r.logicalId}`);
            expectAbsent(`unscoped apigateway Lambda::Permission in ${name}`, found, {
                // Control: the query itself finds permissions, so an empty `found` means every
                // apigateway-principal permission is scoped rather than that none was emitted.
                description: `${name} emits apigateway-principal Lambda permissions at all`,
                count: apiGatewayPermissions(s).length,
            });
        });
    }

    test.each(ALL_TEMPLATES)(
        "FIX-024 control: %s scopes the anonymous bootstrap Lambdas' permission to this API",
        (name) => {
            const s = synth(name);
            const scoped = scopedPermissions(s);

            // This is what keeps the two anonymous routes invokable now that their own unscoped grants
            // are gone: both are registered in RouteRegistry, so the registry loop emits a scoped
            // permission for each. Should that stop holding, GET /api/amplify-config returns 500 — the
            // SPA's bootstrap call, so the login page never renders on either CloudFront or ALB.
            const scopedFns = scoped.map((r) => SynthResult.flatten(r.properties.FunctionName));
            expect(scopedFns.some((fn) => /AmplifyConfigLambda/.test(fn))).toBe(true);
            expect(scopedFns.some((fn) => /VamsVersionLambda/.test(fn))).toBe(true);

            // The registry loop dedupes by function ARN, so one scoped permission per distinct function.
            expect(new Set(scopedFns).size).toBe(scoped.length);
            expect(scoped.length).toBeGreaterThan(30);
        }
    );

    test.each(ALL_TEMPLATES)(
        "FIX-024: %s builds every permission SourceArn from the deployment's own partition",
        (name) => {
            const s = synth(name);
            const scoped = scopedPermissions(s);
            expect(scoped.length).toBeGreaterThan(30);
            const prefixes = Array.from(
                new Set(
                    scoped.map((r) =>
                        SynthResult.flatten(r.properties.SourceArn).replace(
                            /^(arn:[^:]*:[^:]*:[^:]*:[^:]*:).*$/,
                            "$1"
                        )
                    )
                )
            );
            // If the fix adds a sourceArn to the three grants rather than deleting them, a literal
            // `arn:aws:` would silently mis-scope in aws-us-gov and aws-eusc: the permission matches
            // nothing and the function becomes un-invokable. Pinning the whole prefix set catches that.
            expect(prefixes).toEqual([`arn:${s.partition}:execute-api:${s.region}:123456789012:`]);
        }
    );

    test.each(ALL_TEMPLATES)(
        "FIX-024 precondition: %s sets authorizerCredentials on BOTH security schemes",
        (name) => {
            const s = synth(name);
            const apis = s.ofType("AWS::ApiGateway::RestApi");
            expect(apis.length).toBe(1);
            const schemes = apis[0].properties.Body?.components?.securitySchemes;
            expect(Object.keys(schemes ?? {}).sort()).toEqual([
                "VamsAnonymousAuthorizer",
                "VamsAuthorizer",
            ]);

            // The authorizer's own unscoped grant is safe to delete ONLY because API Gateway assumes
            // authInvokeRole to invoke it. If either scheme ever omits authorizerCredentials, removing
            // that grant leaves API Gateway unable to invoke the authorizer and EVERY route fails.
            for (const schemeName of ["VamsAuthorizer", "VamsAnonymousAuthorizer"]) {
                const authorizer = schemes[schemeName]["x-amazon-apigateway-authorizer"];
                expect(SynthResult.flatten(authorizer.authorizerCredentials)).toMatch(
                    /RestAuthorizerInvokeRole.*\.Arn/
                );
                // Same partition trap as the SourceArn above, on the spec side.
                expect(SynthResult.flatten(authorizer.authorizerUri)).toContain(
                    `arn:${s.partition}:apigateway:${s.region}:lambda:path/`
                );
            }
        }
    );
});

/* ------------------------------------------------------------------------------------------------ *
 * FIX-031 — no Lambda points at a backend module that does not exist
 * ------------------------------------------------------------------------------------------------ */

const BACKEND_ROOT = path.join(__dirname, "..", "..", "..", "backend", "backend");

/** `handlers.assets.assetService.lambda_handler` -> `handlers/assets/assetService.py` */
function handlerModulePath(handler: string): string {
    const parts = handler.split(".");
    parts.pop(); // the entry-point function name (lambda_handler, lambda_handler_created, ...)
    return path.join(BACKEND_ROOT, ...parts) + ".py";
}

const backendHandlerFunctions = (s: SynthResult) =>
    s.where("AWS::Lambda::Function", (r) =>
        /^handlers\.[A-Za-z0-9_.]+\.[A-Za-z0-9_]+$/.test(SynthResult.flatten(r.properties.Handler))
    );

describe("FIX-031 no Lambda is wired to a missing backend handler module", () => {
    /**
     * `buildAssetLinksMetadataFunction` pointed at `handlers.assetLinks.assetLinksMetadataService`,
     * which does not exist in `backend/backend`, and has been deleted. No nested stack ever called it,
     * so no Lambda was emitted and this assertion passed before the deletion as well — the defect was
     * dead code, not a broken template. Kept as the regression guard that turns wiring up a missing
     * module into a test failure rather than a runtime `Unable to import module`.
     */
    test.each(ALL_TEMPLATES)("%s emits no assetLinksMetadataService Lambda", (name) => {
        const s = synth(name);
        const assetLinkFns = backendHandlerFunctions(s).filter((r) =>
            /^handlers\.assetLinks\./.test(SynthResult.flatten(r.properties.Handler))
        );
        const missing = assetLinkFns.filter(
            (r) =>
                SynthResult.flatten(r.properties.Handler) ===
                "handlers.assetLinks.assetLinksMetadataService.lambda_handler"
        );
        expectAbsent(
            `assetLinksMetadataService Lambda in ${name}`,
            missing.map((r) => `${r.stack}/${r.logicalId}`),
            {
                // Control: the two real asset-link Lambdas ARE found by the same query, so a zero above
                // means the handler is absent rather than the query being wrong.
                description: `${name} emits handlers.assetLinks.* Lambdas`,
                count: assetLinkFns.length,
            }
        );
        expect(assetLinkFns.map((r) => SynthResult.flatten(r.properties.Handler)).sort()).toEqual([
            "handlers.assetLinks.assetLinksService.lambda_handler",
            "handlers.assetLinks.createAssetLink.lambda_handler",
        ]);
    });

    test.each(ALL_TEMPLATES)("%s: every handlers.* Lambda resolves to a real module", (name) => {
        const s = synth(name);
        const fns = backendHandlerFunctions(s);
        // Control for the "no unresolvable handler" negative below.
        expect(fns.length).toBeGreaterThan(40);
        const unresolvable = fns
            .map((r) => ({ r, handler: SynthResult.flatten(r.properties.Handler) }))
            .filter(({ handler }) => !fs.existsSync(handlerModulePath(handler)))
            .map(({ r, handler }) => `${r.stack}/${r.logicalId} -> ${handler}`);
        expect(unresolvable).toEqual([]);
    });

    test("the Physna hybrid's addon handlers resolve too", () => {
        // The addon lambdas (handlers.addon.physna.*) are only emitted when the add-on is enabled, so the
        // shipped-template pass above never looks at them.
        const s = physnaSynth("commercial");
        const addonFns = backendHandlerFunctions(s).filter((r) =>
            /^handlers\.addon\./.test(SynthResult.flatten(r.properties.Handler))
        );
        expect(addonFns.length).toBeGreaterThan(0);
        const unresolvable = addonFns
            .map((r) => SynthResult.flatten(r.properties.Handler))
            .filter((handler) => !fs.existsSync(handlerModulePath(handler)));
        expect(unresolvable).toEqual([]);
    });
});

/* ------------------------------------------------------------------------------------------------ *
 * FIX-015 — Physna sync queues need a DLQ and partial-batch failure reporting
 * ------------------------------------------------------------------------------------------------ */

const PHYSNA_QUEUE_SUFFIXES = ["physnaFileSync", "physnaAssetSync"];

const physnaSourceQueues = (s: SynthResult) =>
    s.where("AWS::SQS::Queue", (q) =>
        PHYSNA_QUEUE_SUFFIXES.some((suffix) =>
            SynthResult.flatten(q.properties.QueueName).endsWith(`-${suffix}`)
        )
    );

/** Event source mappings whose EventSourceArn is one of the two Physna queues. */
const physnaMappings = (s: SynthResult) => {
    const queueIds = new Set(physnaSourceQueues(s).map((q) => q.logicalId));
    return s.where("AWS::Lambda::EventSourceMapping", (m) =>
        Array.from(queueIds).some((id) =>
            SynthResult.flatten(m.properties.EventSourceArn).includes(id)
        )
    );
};

/** The DLQ resource each Physna source queue redrives to, resolved through its RedrivePolicy. */
const physnaDlqs = (s: SynthResult) => {
    const targets = physnaSourceQueues(s)
        .map((q) => SynthResult.flatten(q.properties.RedrivePolicy?.deadLetterTargetArn))
        .filter((arn) => arn !== "");
    return s.where("AWS::SQS::Queue", (q) => targets.some((arn) => arn.includes(q.logicalId)));
};

describe("FIX-015 Physna sync queues surface failures instead of discarding them", () => {
    test("FIX-015 control: the shipped templates emit no Physna resources at all", () => {
        // Proves the assertions below are matching Physna's own resources and not some other queue in the
        // stack, and that the hybrid config is what makes them observable.
        const hybrid = physnaSynth("commercial");
        expect(physnaSourceQueues(hybrid).length).toBe(2);
        expect(physnaMappings(hybrid).length).toBe(2);

        const shipped = synth("commercial");
        expect(shipped.countOfType("AWS::SQS::Queue")).toBeGreaterThan(0);
        expectAbsent(
            "Physna queue in the shipped commercial template",
            physnaSourceQueues(shipped).map((q) => q.logicalId),
            {
                description: "commercial + Physna hybrid emits Physna queues",
                count: physnaSourceQueues(hybrid).length,
            }
        );
        expect(physnaMappings(shipped)).toEqual([]);
    });

    for (const name of ["commercial", "govcloud"] as TemplateName[]) {
        test(`FIX-015: ${name} + Physna — both event source mappings report batch item failures`, () => {
            // Asserted on the EMITTED property, not the construct call: without it Lambda
            // ignores batchItemFailures and a handler returning 200 for a failed record has
            // its message deleted. The govCloud branch builds lambda.EventSourceMapping
            // directly while the commercial one goes through addEventSource() — SEPARATE code
            // paths, which is why both partitions are asserted.
            const s = physnaSynth(name);
            const mappings = physnaMappings(s);
            expect(mappings.length).toBe(2);
            for (const m of mappings) {
                expect(m.properties.FunctionResponseTypes).toEqual(["ReportBatchItemFailures"]);
            }
        });

        test(`FIX-015: ${name} + Physna — each source queue redrives to its own DLQ`, () => {
            // Both source queues carry a RedrivePolicy, and the AwsSolutions-SQS3 suppression is
            // scoped to the two DLQ resources instead of the stack, so a source queue added later
            // without a DLQ is still reported by CDK Nag.
            const s = physnaSynth(name);
            const queues = physnaSourceQueues(s);
            expect(queues.length).toBe(2);
            const withoutRedrive = queues
                .filter((q) => q.properties.RedrivePolicy === undefined)
                .map((q) => SynthResult.flatten(q.properties.QueueName));
            expect(withoutRedrive).toEqual([]);
            // One DLQ each, not one shared: a shared DLQ hides which sync failed.
            expect(physnaDlqs(s).length).toBe(2);
            expect(
                new Set(
                    queues.map((q) =>
                        SynthResult.flatten(q.properties.RedrivePolicy?.deadLetterTargetArn)
                    )
                ).size
            ).toBe(2);
            // A redrive policy with no maxReceiveCount moves nothing, so the DLQ would exist and
            // stay permanently empty while the source queue kept recycling the same message.
            for (const q of queues) {
                expect(q.properties.RedrivePolicy?.maxReceiveCount).toBeGreaterThan(0);
            }
            // The DLQs are separate resources from the source queues, not the source queues
            // pointing at themselves.
            const sourceIds = new Set(queues.map((q) => q.logicalId));
            expect(physnaDlqs(s).filter((d) => sourceIds.has(d.logicalId))).toEqual([]);
        });
    }

    test("FIX-015: commercial + Physna — DLQs repeat the SQS-managed encryption branch", () => {
        // The source queues take KMS + encryptionMasterKey only when the CMK exists. The
        // commercial template ships useKmsCmkEncryption.enabled = false, so both the queues
        // and their DLQs must fall back to SQS-managed SSE.
        const s = physnaSynth("commercial");
        expect(physnaSourceQueues(s).every((q) => q.properties.SqsManagedSseEnabled)).toBe(true);
        const dlqs = physnaDlqs(s);
        expect(dlqs.length).toBe(2);
        for (const q of dlqs) {
            expect(q.properties.SqsManagedSseEnabled).toBe(true);
            expect(q.properties.KmsMasterKeyId).toBeUndefined();
        }
    });

    test("FIX-015: govcloud + Physna — DLQs repeat the shared-CMK encryption branch", () => {
        // govcloud/eusovereign ship useKmsCmkEncryption.enabled = true. A DLQ that misses this branch is
        // unencrypted under the CMK config.
        const s = physnaSynth("govcloud");
        expect(
            physnaSourceQueues(s).every(
                (q) => SynthResult.flatten(q.properties.KmsMasterKeyId) !== ""
            )
        ).toBe(true);
        const dlqs = physnaDlqs(s);
        expect(dlqs.length).toBe(2);
        for (const q of dlqs) {
            expect(SynthResult.flatten(q.properties.KmsMasterKeyId)).toMatch(/EncryptionKMSKey/);
        }
    });

    test("FIX-015: govcloud + Physna keeps Tags off both event source mappings", () => {
        // No shipped template enables Physna, so the add-on's govCloud EventSourceMapping branch — the
        // single most expensive partition defect class in the codebase — is covered by nothing else.
        // Whichever way the fix adds FunctionResponseTypes, the Tags deletion override must survive.
        const s = physnaSynth("govcloud");
        const mappings = physnaMappings(s);
        expect(mappings.length).toBe(2);
        expectAbsent(
            "Physna EventSourceMapping with Tags in govcloud",
            mappings.filter((m) => "Tags" in m.properties).map((m) => m.logicalId),
            { description: "govcloud + Physna emits Physna mappings", count: mappings.length }
        );

        // Control that the property is emitted at all when the partition allows it: the commercial
        // hybrid goes through addEventSource(), which stamps the stack tags.
        const commercialMappings = physnaMappings(physnaSynth("commercial"));
        expect(commercialMappings.filter((m) => "Tags" in m.properties).length).toBe(2);
    });
});
