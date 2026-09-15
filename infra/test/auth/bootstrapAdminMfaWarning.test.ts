/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * FIX-071 — the seeded bootstrap `admin` role is written with `mfaRequired: false` and the user pool
 * sets `Mfa.OPTIONAL`, so the administrator account named by `app.adminUserId` reaches every
 * administrative route with a password alone. Enrolling MFA is an operator step VAMS cannot perform at
 * deploy time, so the posture stays as it is and the deployment reports it.
 *
 * Owner constraint (verbatim): "Make admin by default not MFA required in cognito as enrollment is a
 * bigger effort. Output warning though that bootstrap admin is not MFA protected by default." The
 * `mfaRequired: false` assertion below is therefore a required behaviour, not an accident to be tidied
 * up: a change that starts requiring MFA on the seeded role locks the operator out of the deployment
 * they just created, and that is what it catches.
 *
 * ## Why a synth-time annotation and not a CfnOutput
 *
 * `Annotations.of(scope).addWarningV2` is what `wafv2-basic-construct.ts:154` already uses for an
 * operator-facing warning, and the AWS CDK CLI prints annotations while synthesizing — before and
 * during `cdk deploy` — where the operator is looking. A `CfnOutput` is only visible after the stack
 * has finished, mixed in with ~20 other outputs.
 *
 * ## Why the warning survives being emitted from inside a nested stack
 *
 * The construct is instantiated inside `AuthBuilderNestedStack`, and a nested stack is not a cloud
 * assembly artifact of its own. Verified against the assembly rather than assumed: a warning added by
 * a construct inside a `NestedStack` is written to `<TopLevelStack>.metadata.json` keyed by the full
 * construct path, and `assertions.Annotations.fromStack(topLevelStack)` reads exactly that file. This
 * suite instantiates the construct directly under a top-level `Stack` because `Annotations.fromStack`
 * needs a stack with an artifact id; the annotation call it exercises is the same one.
 */

import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import { DynamoDbAuthDefaultsAdminConstructStack } from "../../lib/nestedStacks/auth/constructs/dynamodb-authdefaults-admin-construct";
import { newTestApp } from "../support/testApp";

const ADMIN_USER_ID = "t071-admin@example.com";

/**
 * The construct reads three table names, `config.app.adminUserId`, and a role for its custom
 * resources. Everything else on `storageResources` and `Config` is unused here, so a narrow stand-in
 * keeps this off the full-app synth path.
 */
function synthConstruct(
    useCognito = true,
    federated = false
): { stack: cdk.Stack; template: Template } {
    const app = newTestApp();
    const stack = new cdk.Stack(
        app,
        `AuthDefaultsTestStack${useCognito ? "Cognito" : "External"}${federated ? "Federated" : ""}`
    );
    const customResourceRole = new iam.Role(stack, "CustomResourceRole", {
        assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });

    new DynamoDbAuthDefaultsAdminConstructStack(stack, "AuthDefaultsAdmin", {
        customResourceRole,
        lambdaCommonBaseLayer: undefined as any,
        storageResources: {
            dynamo: {
                rolesStorageTable: { tableName: "t071-roles" },
                userRolesStorageTable: { tableName: "t071-user-roles" },
                constraintsStorageTable: { tableName: "t071-constraints" },
            },
        } as any,
        config: {
            app: {
                adminUserId: ADMIN_USER_ID,
                authProvider: {
                    useCognito: {
                        enabled: useCognito,
                        useSaml: federated,
                        useOidc: false,
                    },
                },
            },
        } as any,
    });

    return { stack, template: Template.fromStack(stack) };
}

/** The `putItem` payloads the construct's `Custom::AWS` resources carry, parsed. */
function putItemPayloads(template: Template): any[] {
    return Object.values(template.findResources("Custom::AWS")).map((r: any) => {
        const text = r.Properties.Create ?? r.Properties.Update;
        if (typeof text !== "string") {
            throw new Error(
                `expected a literal SDK-call payload, got ${JSON.stringify(text).slice(0, 200)}`
            );
        }
        return JSON.parse(text);
    });
}

describe("FIX-071 bootstrap admin MFA posture", () => {
    const { stack, template } = synthConstruct();
    const payloads = putItemPayloads(template);

    test("the construct emits the seeded records at all", () => {
        // Control for everything below. A construct that threw or emitted nothing would leave the
        // annotation assertion reading an empty message list and the mfaRequired assertion scanning
        // an empty payload set.
        expect(payloads.length).toBeGreaterThan(10);
        expect(payloads.every((p) => p.action === "putItem")).toBe(true);
    });

    test("the seeded admin role still does NOT require MFA", () => {
        // Owner constraint. Also the over-tightening catcher: the accepted fix is a warning, not a
        // posture change, so flipping this to true here (or requiring MFA on the user pool) must fail.
        const roleRows = payloads.filter((p) => "roleName" in (p.parameters?.Item ?? {}));
        expect(roleRows.length).toBeGreaterThan(0);
        const adminRole = roleRows.find(
            (p) => p.parameters.Item.id?.S === "initial_admin_role_creation"
        );
        expect(adminRole).toBeDefined();
        expect(adminRole.parameters.Item.mfaRequired).toEqual({ BOOL: false });
    });

    test("FIX-071: synth warns that the bootstrap administrator is not MFA protected", () => {
        Annotations.fromStack(stack).hasWarning(
            "*",
            Match.stringLikeRegexp("bootstrap administrator .* is not MFA protected")
        );
    });

    test("the warning names the configured administrator and the seeded role", () => {
        // The message has to be actionable on its own: an operator reading deploy output needs the
        // account and the role, not a generic "MFA is optional" note.
        const found = Annotations.fromStack(stack).findWarning(
            "*",
            Match.stringLikeRegexp("not MFA protected")
        );
        expect(found.length).toBe(1);
        const message = String(found[0].entry.data);
        expect(message).toContain(ADMIN_USER_ID);
        expect(message).toContain("mfaRequired");
        expect(found[0].id).toContain("AuthDefaultsAdmin");
    });

    test("no warning when an external OAuth IDP owns the credential", () => {
        // Conditionality. With `useCognito.enabled` false there is no VAMS-managed password to leave
        // unprotected, and the MFA claim the authorizer resolves is always false for an external IDP —
        // so the remediation the message recommends would make an mfaRequired role inactive rather
        // than safer. The seeded records are emitted either way, which is the control: the construct
        // ran, it simply did not warn.
        const external = synthConstruct(false);
        expect(putItemPayloads(external.template).length).toBe(payloads.length);
        Annotations.fromStack(external.stack).hasNoWarning(
            "*",
            Match.stringLikeRegexp("not MFA protected")
        );
    });

    test("federation is NOT treated as a reason to suppress the warning", () => {
        // The bootstrap administrator is a native user pool user (`CfnUserPoolUser` "AdminUser" in
        // cognito-web-native-construct.ts, created whenever Cognito is enabled), and a federated pool
        // still accepts username/password for its native users — `cli/commands/setup-and-auth.md` says
        // so explicitly. So enabling SAML or OIDC does not hand that password to the provider, and the
        // warning still applies. The gate reads `useCognito.enabled` alone, which this pins.
        const federated = synthConstruct(true, true);
        Annotations.fromStack(federated.stack).hasWarning(
            "*",
            Match.stringLikeRegexp("not MFA protected")
        );
    });

    test("the matcher used above is selective, not a catch-all", () => {
        // Positive control for the two assertions above: `hasWarning`/`findWarning` throw or return
        // nothing when a message is absent, so a matcher that happened to match every annotation
        // (the AwsCustomResource SDK-version warning is also present in this stack) would make them
        // pass without the fix. This proves the same reader reports absence when it should.
        Annotations.fromStack(stack).hasNoWarning(
            "*",
            Match.stringLikeRegexp("no annotation in this stack says this")
        );
        expect(
            Annotations.fromStack(stack).findWarning(
                "*",
                Match.stringLikeRegexp("no annotation in this stack says this")
            ).length
        ).toBe(0);
    });
});
