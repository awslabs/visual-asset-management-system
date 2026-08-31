/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: key retention, VPC interface endpoints, Lambda ephemeral storage and IAM trust conditions.
 *
 * Six fixes are covered here that the fix plan assigned to the T2 (deploy + smoke) tier even though the
 * property each one changes is visible in the emitted CloudFormation. Asserting them at T1 means they are
 * validated across all three partitions now, rather than on the single commercial dev stack later:
 *
 *  - FIX-001 (S1-INFRA-012, CRITICAL) — the VAMS-generated CMK must carry `RemovalPolicy.RETAIN` to match
 *    the tables and buckets it encrypts, so `cdk destroy` cannot leave retained data undecryptable.
 *  - FIX-011 (S1-INFRA-006) — the physna sync lambdas stage asset files in /tmp, so they carry more than
 *    the default 512 MB of ephemeral storage. Both are asserted: `physnaAssetSync` repairs drift through
 *    `physnaFileSync._upload_file_to_physna`, so it stages the same way and a budget on only one of them
 *    leaves the same file failing depending on which event delivered it. This also covers FIX-063
 *    (S2-BACKEND-126), which reports the same unbounded /tmp staging from the handler side and resolves to
 *    the same ephemeral-storage property — the handler still reads a staged file whole, so memory bounds
 *    the supported file size and the storage size only has to clear it.
 *  - FIX-034 (S1-INFRA-002) — the base64 export defect must NOT be fixed with a wildcard
 *    `binaryMediaTypes` entry; this is the regression guard for the approach the owner ruled out, not for
 *    the fix itself.
 *  - FIX-037 (S1-INFRA-030) — a Secrets Manager interface endpoint is part of the core endpoint set, so
 *    reading a secret from an isolated subnet does not hang.
 *  - FIX-039 (S1-INFRA-083) — the authorizer invoke role's trust carries an `aws:SourceAccount` condition
 *    in the fail-open operator form, and carries no `aws:SourceArn`. Whether the condition has any EFFECT
 *    is not decidable at T1 — see the block comment on that describe.
 *  - FIX-072 (S1-INFRA-114) — the workflow state-machine role carries no `iam:PassRole`, while the
 *    workflowService lambda keeps the one `states:CreateStateMachine` requires.
 *
 * ## Two traps this file is built around
 *
 * **The shipped templates disable most of what is under assertion.** `useKmsCmkEncryption.enabled` is
 * FALSE in `config.template.commercial.json` and true only in the two restricted templates, so the CMK
 * assertions run against govcloud/eusovereign — and FIX-001 does not affect a default commercial
 * deployment at all. `usePhysnaSync.enabled` is false everywhere, so FIX-011 needs a keyed hybrid config.
 * An assertion written against a shipped template without checking this finds nothing and passes
 * vacuously, which is the failure mode this tier exists to remove.
 *
 * **FIX-072 has two `iam:PassRole` statements that are NOT interchangeable**, and the fix plan's own
 * file:line header points at the wrong one — see the block comment on that describe. One assertion targets
 * the removable statement and a second pins the one that must survive, because a fix that deletes both
 * takes workflow creation down with an AccessDenied on `states:CreateStateMachine`.
 *
 * Every assertion here is written in its POST-fix state and runs as an ordinary test. `expectAbsent` must
 * never be used inside an `it.failing` test — its control inverts into a false green — which is why the
 * absence assertions below use a plain `expect` with the control asserted in an adjacent test; that
 * structure stays correct now that the markers are off.
 */

import {
    RESTRICTED_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";

// A full-app synth is ~20 s and this file needs four of them (three shipped + one physna hybrid).
jest.setTimeout(600_000);

const synth = (name: TemplateName): SynthResult => synthTemplate(name);

/** Physna hybrid — the add-on is disabled in every shipped template, so its Lambdas are never emitted. */
const PHYSNA_TENANT_ID = "11111111-2222-3333-4444-555555555555";
const enablePhysna = (c: any) => {
    c.app.addons.usePhysnaSync.enabled = true;
    c.app.addons.usePhysnaSync.tenantId = PHYSNA_TENANT_ID;
};
const synthPhysna = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enablePhysna, mutateKey: "physna-enabled" });

/** Templates whose config actually creates a VAMS-generated CMK. Commercial ships with CMK off. */
const CMK_TEMPLATES: TemplateName[] = RESTRICTED_TEMPLATES;

/**
 * Every IAM statement in a template, from all three places CDK can put one.
 *
 * Scanning only `AWS::IAM::Policy` under-reports: CDK spills statements into an
 * `AWS::IAM::ManagedPolicy` once an inline policy approaches the size limit, and role-level
 * `inlinePolicies` land on the role itself. A scan that misses either reports a present grant as absent.
 */
function allStatements(
    s: SynthResult
): { resource: Resource; policyName: string; statement: any }[] {
    const out: { resource: Resource; policyName: string; statement: any }[] = [];
    const push = (resource: Resource, policyName: string, doc: any) => {
        for (const statement of doc?.Statement ?? []) out.push({ resource, policyName, statement });
    };
    for (const r of s.ofType("AWS::IAM::Policy")) {
        push(r, String(r.properties.PolicyName ?? r.logicalId), r.properties.PolicyDocument);
    }
    for (const r of s.ofType("AWS::IAM::ManagedPolicy")) {
        push(r, String(r.properties.ManagedPolicyName ?? r.logicalId), r.properties.PolicyDocument);
    }
    for (const r of s.ofType("AWS::IAM::Role")) {
        for (const p of r.properties.Policies ?? []) {
            push(r, String(p.PolicyName ?? r.logicalId), p.PolicyDocument);
        }
    }
    return out;
}

function actionsOf(statement: any): string[] {
    const a = statement?.Action;
    return Array.isArray(a) ? a.map(String) : a === undefined ? [] : [String(a)];
}

// ---------------------------------------------------------------------------------------------------
// FIX-001 — the VAMS-generated CMK must survive a stack teardown
// ---------------------------------------------------------------------------------------------------

describe("FIX-001: retained data stays decryptable after cdk destroy", () => {
    const vamsKeys = (s: SynthResult): Resource[] =>
        s.where("AWS::KMS::Key", (r) =>
            String(r.properties.Description ?? "").includes("VAMS Generated KMS Encryption key")
        );

    test.each(CMK_TEMPLATES)("%s emits exactly one VAMS-generated CMK", (name) => {
        // Control for the ratchet below: without a key there is no DeletionPolicy to assert.
        expect(vamsKeys(synth(name))).toHaveLength(1);
    });

    test.each(CMK_TEMPLATES)("%s DeletionPolicy is populated on retained storage", (name) => {
        // Second control: proves DeletionPolicy is emitted at all in this assembly, so "Retain" being
        // absent below is a real finding and not a template that omits the field everywhere.
        const s = synth(name);
        const retainedTables = s
            .ofType("AWS::DynamoDB::Table")
            .filter((r) => r.raw.DeletionPolicy === "Retain");
        expect(retainedTables.length).toBeGreaterThan(0);
    });

    it("commercial ships with CMK encryption disabled, so it is unaffected", () => {
        // Recorded as a test because it bounds the finding's severity: the CRITICAL rating applies to
        // deployments that turn CMK encryption on, not to a default commercial deploy.
        expect(vamsKeys(synth("commercial"))).toHaveLength(0);
        expect(synth("commercial").countOfType("AWS::DynamoDB::Table")).toBeGreaterThan(0);
    });

    it.each(CMK_TEMPLATES)(
        "%s: the VAMS-generated CMK carries DeletionPolicy Retain",
        (name: TemplateName) => {
            const key = vamsKeys(synth(name))[0];
            expect(key.raw.DeletionPolicy).toBe("Retain");
            // UpdateReplacePolicy too: an update that replaces the key (for example switching to
            // optionalExternalCmkArn) must not schedule the old key for deletion while retained data
            // is still encrypted under it.
            expect(key.raw.UpdateReplacePolicy).toBe("Retain");
        }
    );
});

// ---------------------------------------------------------------------------------------------------
// FIX-011 — /tmp downloaders need more than the default 512 MB
// ---------------------------------------------------------------------------------------------------

describe("FIX-011: the physna sync lambdas have ephemeral storage for large CAD files", () => {
    // Both sync lambdas stage a file in /tmp: physnaAssetSync repairs drift by calling
    // physnaFileSync._upload_file_to_physna, so it runs the same download-then-read path and needs the
    // same disk. Only the file-sync half is reachable from the handler name, so each is matched
    // separately rather than by a shared "physna" substring, which would also catch physnaViewer.
    const syncLambda =
        (handler: string) =>
        (s: SynthResult): Resource[] =>
            s.where("AWS::Lambda::Function", (r) =>
                String(r.properties.Handler ?? "").includes(`physna.${handler}`)
            );

    const fileSync = syncLambda("physnaFileSync");
    const assetSync = syncLambda("physnaAssetSync");

    it("the physna hybrid config emits both sync lambdas", () => {
        // Control: the shipped templates disable the add-on, so without the hybrid this describe would
        // assert against an empty list and pass while checking nothing.
        expect(fileSync(synthPhysna("commercial"))).toHaveLength(1);
        expect(assetSync(synthPhysna("commercial"))).toHaveLength(1);
    });

    it("no shipped template emits them, which is why the hybrid is required", () => {
        expectAbsent(
            "physna sync lambdas in the shipped commercial template",
            [...fileSync(synth("commercial")), ...assetSync(synth("commercial"))],
            {
                description: "lambdas emitted by the commercial template",
                count: synth("commercial").countOfType("AWS::Lambda::Function"),
            }
        );
    });

    it.each([
        ["physnaFileSync", fileSync],
        ["physnaAssetSync", assetSync],
    ])("%s declares more than the default 512 MB of ephemeral storage", (_name, select) => {
        // Scoped to the cited sites. The audit the owner asked for also covers pipeline lambdas that
        // download to /tmp, but which of those do so is not visible in the template — that half belongs
        // in a code-level review, not a synth assertion.
        const fn = select(synthPhysna("commercial"))[0];
        expect(fn.properties.EphemeralStorage?.Size ?? 512).toBeGreaterThan(512);
    });

    it("both sync lambdas get the same budget, since they share the upload path", () => {
        const sizeOf = (select: (s: SynthResult) => Resource[]) =>
            select(synthPhysna("commercial"))[0].properties.EphemeralStorage?.Size ?? 512;
        expect(sizeOf(assetSync)).toBe(sizeOf(fileSync));
    });
});

// ---------------------------------------------------------------------------------------------------
// FIX-034 — guard against the approach the owner ruled out
// ---------------------------------------------------------------------------------------------------

describe("FIX-034: the base64 export fix does not go through binaryMediaTypes", () => {
    // The owner: "We already tried setting binaryMediaTypes: ['*/*'] and it didn't fix it or it broke
    // many other things." So this is an ordinary passing guard on the WRONG fix, not a ratchet on the
    // right one — the real fix (presigned URL, as the download and stream APIs already do) lands in the
    // backend handler and is asserted in the v260 smoke suite instead.
    //
    // BOTH surfaces have to be checked, and the obvious one is the wrong one. VAMS builds a
    // `SpecRestApi` from `ApiDefinition.fromInline(spec)`, and `SpecRestApiProps` does not surface
    // `binaryMediaTypes` at all — so the ruled-out fix would be written into the OpenAPI document as
    // `x-amazon-apigateway-binary-media-types` and emitted inside the RestApi's `Body`, never as the
    // `BinaryMediaTypes` CloudFormation property. A guard that checked only the property would pass
    // while the ruled-out change was live, which is worse than no guard. The CFN property is checked
    // too, in case the API is ever migrated to the non-spec L2.
    const WILDCARD = "*" + "/*";

    test.each(["commercial", ...RESTRICTED_TEMPLATES] as TemplateName[])(
        "%s declares no wildcard binary media types, on either surface",
        (name) => {
            const s = synth(name);
            const wildcard = s.where("AWS::ApiGateway::RestApi", (r) => {
                const fromProperty = (r.properties.BinaryMediaTypes ?? []).some((t: any) =>
                    SynthResult.flatten(t).includes(WILDCARD)
                );
                const declared =
                    r.properties.Body?.["x-amazon-apigateway-binary-media-types"] ?? [];
                const fromSpec = SynthResult.flatten(declared).includes(WILDCARD);
                return fromProperty || fromSpec;
            });
            expectAbsent(`a REST API declaring '${WILDCARD}' as a binary media type`, wildcard, {
                description: "REST APIs emitted",
                count: s.countOfType("AWS::ApiGateway::RestApi"),
            });
        }
    );

    it("the OpenAPI document really is inlined into Body, so the spec surface is reachable", () => {
        // Control for the spec half above. If the definition were switched to an S3 location the Body
        // property would disappear and that half of the guard would silently stop checking anything.
        const apis = synth("commercial").ofType("AWS::ApiGateway::RestApi");
        expect(apis).toHaveLength(1);
        expect(apis[0].properties.Body?.openapi).toBe("3.0.1");
    });
});

// ---------------------------------------------------------------------------------------------------
// FIX-037 — Secrets Manager interface endpoint
// ---------------------------------------------------------------------------------------------------

describe("FIX-037: a Secrets Manager interface endpoint exists for isolated subnets", () => {
    const endpointNames = (s: SynthResult): string[] =>
        s.ofType("AWS::EC2::VPCEndpoint").map((r) => SynthResult.flatten(r.properties.ServiceName));

    // Both restricted templates enable the global VPC; commercial ships with it off, so it emits no
    // interface endpoints and is excluded rather than asserted vacuously.
    test.each(RESTRICTED_TEMPLATES)("%s emits interface endpoints at all", (name) => {
        expect(synth(name).countOfType("AWS::EC2::VPCEndpoint")).toBeGreaterThan(0);
    });

    it.each(RESTRICTED_TEMPLATES)(
        "%s emits a secretsmanager interface endpoint",
        (name: TemplateName) => {
            // The endpoint is a CORE endpoint, not one conditional on the Physna add-on, so it is
            // asserted against the shipped template rather than the hybrid.
            const matches = endpointNames(synth(name)).filter((n) => /secretsmanager/i.test(n));
            expect(matches.length).toBeGreaterThan(0);
        }
    );
});

// ---------------------------------------------------------------------------------------------------
// FIX-039 — authorizer invoke role trust condition
// ---------------------------------------------------------------------------------------------------

/**
 * ## What these assertions can and cannot establish
 *
 * **They cannot establish that the condition has any effect, and no synth assertion can.** The trust
 * condition only bites if API Gateway actually puts `aws:SourceAccount` into the request context of the
 * `sts:AssumeRole` it makes for `authorizerCredentials` (buildOpenApiSpec.ts:119,142 — the sole path to
 * the authorizer). Whether it does is undocumented: the IAM reference says these keys are present "only
 * when the call to your resource is being made directly by an AWS service principal on behalf of a
 * resource for which the configuration triggered the service-to-service request" and adds that "not all
 * service integrations require the use of this global condition key"; the Amazon API Gateway Developer
 * Guide has no cross-service confused-deputy page at all, and both apigateway-assumable roles it does
 * document (the AWS Service integration credentials role, the Marketplace metering role) are shown with
 * unconditioned trust policies. `aws-cdk-lib`'s own `LambdaAuthorizer.setupPermissions()` attaches only
 * an identity policy when handed an `assumeRole` and adds no trust condition either.
 *
 * So the condition is either enforced or vacuous, and which one cannot be decided offline. What IS
 * checkable here is the shape that keeps the undecidable case SAFE rather than an outage, and that is
 * what the tests below pin:
 *
 *  - the operator is the `...IfExists` form, so an absent key evaluates true instead of denying;
 *  - the key is `aws:SourceAccount`, which has one possible value shape, and the value is this
 *    deployment's account;
 *  - there is no `aws:SourceArn`, whose value shape for an API Gateway resource is ambiguous (the
 *    `execute-api` data-plane ARN vs. the `apigateway` control-plane ARN, whose account field is empty),
 *    so an `ArnLike` written for either fails closed against the other — and a fail-closed condition on
 *    this role 500s every route at once, anonymous ones included, since they share this authorizer;
 *  - the trust document embeds no ARN, so it cannot carry a hardcoded partition into GovCloud/EU
 *    Sovereign.
 *
 * **The deploy check this defers to**, and the only thing that closes S1-INFRA-083: after deploying,
 * (1) call an authenticated route with a freshly issued token — a new sign-in, so a new Authorization
 * cache key — and confirm 200 plus a NEW invocation in the authorizer Lambda's log group (a 200 served
 * from the 30s authorizer cache produces no invocation, which is exactly how a broken assume-role
 * hides); (2) read the CloudTrail `AssumeRole` event whose `requestParameters.roleArn` is this role and
 * inspect `requestParameters`/`additionalEventData` for a supplied `aws:SourceAccount`. If the key is
 * absent there, the condition is confirmed vacuous and the finding needs a different mitigation — a
 * Lambda resource permission on the authorizer scoped to
 * `arn:<partition>:execute-api:<region>:<account>:<restApiId>/authorizers/*` with
 * `authorizerCredentials` dropped from both security schemes, which is the mechanism CDK uses when no
 * credentials role is supplied and the one AWS documents as enforced.
 */
describe("FIX-039: the authorizer invoke role restricts who may assume it", () => {
    const invokeRole = (s: SynthResult): Resource[] =>
        s.where("AWS::IAM::Role", (r) => r.logicalId.includes("RestAuthorizerInvokeRole"));

    /** The `Condition` blocks on the role's trust policy statements. */
    const trustConditions = (s: SynthResult): any[] =>
        (invokeRole(s)[0].properties.AssumeRolePolicyDocument?.Statement ?? [])
            .map((st: any) => st.Condition)
            .filter(Boolean);

    test.each(["commercial", ...RESTRICTED_TEMPLATES] as TemplateName[])(
        "%s emits the authorizer invoke role trusting API Gateway in its own partition",
        (name) => {
            // Control, and a partition check in its own right: the trust principal must be the
            // partition-correct API Gateway service principal, which is what makes the condition
            // below assertable per partition.
            const roles = invokeRole(synth(name));
            expect(roles).toHaveLength(1);
            const trust = SynthResult.flatten(roles[0].properties.AssumeRolePolicyDocument);
            expect(trust).toMatch(/apigateway/);
        }
    );

    test.each(["commercial", ...RESTRICTED_TEMPLATES] as TemplateName[])(
        "%s: the trust condition is StringEqualsIfExists on aws:SourceAccount for this account",
        (name) => {
            // Pinned as an exact object rather than a regex on the whole document. A regex for
            // `aws:SourceAccount` passes just as well against `StringEquals` (which DENIES when API
            // Gateway does not supply the key) as against `StringEqualsIfExists`, and the operator is
            // the difference between a safe no-op and a total API outage — so the operator name, the
            // key name and the account value all have to be part of the assertion.
            //
            // The condition KEY carries the name, so this reads the parsed object rather than
            // `SynthResult.flatten`: flatten walks an object down to its VALUES and drops the keys, so
            // a match on `aws:SourceAccount` written that way could never succeed.
            const s = synth(name);
            expect(trustConditions(s)).toEqual([
                { StringEqualsIfExists: { "aws:SourceAccount": "123456789012" } },
            ]);
        }
    );

    test.each(["commercial", ...RESTRICTED_TEMPLATES] as TemplateName[])(
        "%s: the trust names no aws:SourceArn",
        (name) => {
            // The outage guard. An `aws:SourceArn` pattern here is a bet on which of two ARN shapes
            // API Gateway supplies for an API resource, and losing that bet denies the assume-role on
            // every request. Kept separate from the exact-match test above so a future edit that adds
            // a second condition operator fails against a named reason rather than an opaque diff.
            const s = synth(name);
            const withSourceArn = trustConditions(s).filter((c) =>
                JSON.stringify(c).includes("aws:SourceArn")
            );
            expectAbsent(
                "an aws:SourceArn condition on the authorizer invoke role trust",
                withSourceArn,
                {
                    description: "condition blocks present on that trust policy",
                    count: trustConditions(s).length,
                }
            );
        }
    );

    test.each(["commercial", ...RESTRICTED_TEMPLATES] as TemplateName[])(
        "%s: the trust document embeds no ARN, so it carries no hardcoded partition",
        (name) => {
            // A literal `arn:aws:` in a trust condition is correct in commercial and locks GovCloud
            // and EU Sovereign out of assuming the role — an outage in the partitions where it is
            // hardest to notice. The condition needs no ARN at all, so the strongest available check
            // is that none is present, which no partition can get wrong.
            const doc = invokeRole(synth(name))[0].properties.AssumeRolePolicyDocument;
            expect(JSON.stringify(doc)).not.toMatch(/arn:/);
        }
    );
});

// ---------------------------------------------------------------------------------------------------
// FIX-072 — remove the unused PassRole, keep the used one
// ---------------------------------------------------------------------------------------------------

/**
 * The two `iam:PassRole` statements are not interchangeable, and the fix plan's header cites the wrong
 * one (`workflowFunctions.ts:1058`) while its own rationale argues about the other (`:847-851`):
 *
 *  - `:847-851` is inside `runWorkflowPolicy`, an inline policy on `VAMSWorkflowIAMRole` — the role
 *    deployed state machines ASSUME (`buildWorkflowRole`). A state machine passes a role only for
 *    integrations like ECS RunTask or SageMaker, none of which the generated ASL emits. **Removable.**
 *  - `:1058` is in `buildWorkflowServiceV2Function`, on the workflowService LAMBDA's role, alongside
 *    `states:CreateStateMachine`. Creating a state machine with a role REQUIRES `iam:PassRole` on it.
 *    **Must stay** — removing it breaks workflow creation with an AccessDenied.
 *
 * So the ratchet targets the role-level statement and the passing test pins the lambda-level one.
 */
describe("FIX-072: the workflow state-machine role drops its unused PassRole", () => {
    const passRoleOn = (s: SynthResult, wanted: "role" | "lambda") =>
        allStatements(s).filter((e) => {
            if (!actionsOf(e.statement).includes("iam:PassRole")) return false;
            const onWorkflowRole =
                e.resource.type === "AWS::IAM::Role" &&
                e.resource.logicalId.includes("VAMSWorkflowIAMRole");
            return wanted === "role" ? onWorkflowRole : !onWorkflowRole;
        });

    test.each(["commercial", ...RESTRICTED_TEMPLATES] as TemplateName[])(
        "%s: the workflowService lambda keeps iam:PassRole (states:CreateStateMachine needs it)",
        (name) => {
            // This must pass BEFORE and AFTER the fix. It is the guard against an over-broad fix that
            // greps for iam:PassRole and deletes every hit.
            const s = synth(name);
            expect(passRoleOn(s, "lambda").length).toBeGreaterThan(0);
            const creators = allStatements(s).filter((e) =>
                actionsOf(e.statement).includes("states:CreateStateMachine")
            );
            expect(creators.length).toBeGreaterThan(0);
        }
    );

    test.each(["commercial", ...RESTRICTED_TEMPLATES] as TemplateName[])(
        "%s: VAMSWorkflowIAMRole exists and carries runWorkflowPolicy",
        (name) => {
            // Control for the absence ratchet below, in its own passing test: `expectAbsent` cannot be
            // used inside `it.failing` because a control that finds nothing THROWS, which satisfies
            // `.failing` and reports green while asserting nothing.
            const s = synth(name);
            const roles = s.where("AWS::IAM::Role", (r) =>
                r.logicalId.includes("VAMSWorkflowIAMRole")
            );
            expect(roles).toHaveLength(1);
            const policyNames = (roles[0].properties.Policies ?? []).map((p: any) =>
                String(p.PolicyName)
            );
            expect(policyNames).toContain("runWorkflowPolicy");
        }
    );

    it.each(["commercial", ...RESTRICTED_TEMPLATES] as TemplateName[])(
        "%s: VAMSWorkflowIAMRole grants no iam:PassRole",
        (name: TemplateName) => {
            expect(passRoleOn(synth(name), "role")).toEqual([]);
        }
    );
});
