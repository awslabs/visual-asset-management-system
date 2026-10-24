/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * With external OAuth and an MFA scope, the login page offers "Log in with MFA", but the authorizer
 * resolves the MFA claim through the external OAuth slot of `customMFATokenScopeCheckOverride`, which
 * ships returning false. Synth warns once under its own ID while that slot is unchanged, and stays
 * quiet when the slot is customized, when no MFA scope is set, and on a Cognito deployment.
 */

import * as fs from "fs";
import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { Annotations, Match } from "aws-cdk-lib/assertions";
import {
    DynamoDbAuthDefaultsAdminConstructStack,
    EXTERNAL_OAUTH_MFA_HOOK_WARNING_ID,
    MFA_HOOK_SOURCE_PATH,
    isExternalMfaHookDefault,
} from "../../lib/nestedStacks/auth/constructs/dynamodb-authdefaults-admin-construct";
import { newTestApp } from "../support/testApp";

const SHIPPED_HOOK = fs.readFileSync(MFA_HOOK_SOURCE_PATH, "utf8");
const ID_TAG = `[ack: ${EXTERNAL_OAUTH_MFA_HOOK_WARNING_ID}]`;

let stackCounter = 0;

function synth(authProvider: Record<string, unknown>): cdk.Stack {
    const app = newTestApp();
    const stack = new cdk.Stack(app, `ExternalMfaHookWarning${stackCounter++}`);
    const customResourceRole = new iam.Role(stack, "CustomResourceRole", {
        assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });
    new DynamoDbAuthDefaultsAdminConstructStack(stack, "AuthDefaultsAdmin", {
        customResourceRole,
        lambdaCommonBaseLayer: undefined as any,
        storageResources: {
            dynamo: {
                rolesStorageTable: { tableName: "mfa-hook-roles" },
                userRolesStorageTable: { tableName: "mfa-hook-user-roles" },
                constraintsStorageTable: { tableName: "mfa-hook-constraints" },
            },
        } as any,
        config: { app: { adminUserId: "operator@example.com", authProvider } } as any,
    });
    return stack;
}

const externalOAuth = (idpAuthProviderScopeMfa: string | null) => ({
    useCognito: { enabled: false, useSaml: false, useOidc: false },
    useExternalOAuthIdp: {
        enabled: true,
        idpAuthProviderScope: "openid",
        idpAuthProviderScopeMfa,
    },
});

const hookWarnings = (stack: cdk.Stack) =>
    Annotations.fromStack(stack)
        .findWarning("*", Match.stringLikeRegexp(".*"))
        .filter((w) => String(w.entry.data).includes(ID_TAG));

describe("external OAuth MFA hook warning", () => {
    test("the shipped hook's external slot reads as the default (control)", () => {
        expect(isExternalMfaHookDefault(SHIPPED_HOOK)).toBe(true);
    });

    test("external OAuth with an MFA scope and the shipped hook warns exactly once", () => {
        const warnings = hookWarnings(synth(externalOAuth("mfa")));
        expect(warnings.length).toBe(1);
        const message = String(warnings[0].entry.data);
        expect(message).toContain("idpAuthProviderScopeMfa");
        expect(message).toContain("customMFATokenScopeCheckOverride");
        expect(message).toContain("developer/security.md#authentication-override-hooks");
        expect(message).not.toMatch(/admin/i);
        expect(message).not.toContain("not MFA protected");
    });

    test.each([[null], [""], ["   "], ["undefined"]])(
        "an MFA scope of %p gives no warning",
        (scope) => {
            expect(hookWarnings(synth(externalOAuth(scope)))).toEqual([]);
        }
    );

    test("a Cognito deployment gives no warning", () => {
        const stack = synth({ useCognito: { enabled: true, useSaml: false, useOidc: false } });
        expect(hookWarnings(stack)).toEqual([]);
    });

    test("a customized external slot is not the default", () => {
        const customized = SHIPPED_HOOK.replace(
            /(#External OAUTH IDP MFA check\s*\n)(\s*)mfaLoginEnabled = False/,
            "$1$2mfaLoginEnabled = check_idp_userinfo(lambdaRequest)"
        );
        expect(customized).not.toBe(SHIPPED_HOOK);
        expect(isExternalMfaHookDefault(customized)).toBe(false);

        // The construct reads the bundled file at synth; serve it the customized copy.
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const realFs = require("fs");
        const original = realFs.readFileSync;
        const spy = jest
            .spyOn(realFs, "readFileSync")
            .mockImplementation((...args: any[]) =>
                args[0] === MFA_HOOK_SOURCE_PATH ? customized : original.apply(realFs, args)
            );
        try {
            expect(hookWarnings(synth(externalOAuth("mfa")))).toEqual([]);
            expect(spy).toHaveBeenCalledWith(MFA_HOOK_SOURCE_PATH, "utf8");
        } finally {
            spy.mockRestore();
        }
    });

    test("comments, blank lines and CR line endings in the slot still read as the default", () => {
        const noisy = SHIPPED_HOOK.replace(
            /(#External OAUTH IDP MFA check\s*\n)/,
            "$1            # placeholder until the IdP exposes amr\n\n"
        ).replace(/\n/g, "\r\n");
        expect(isExternalMfaHookDefault(noisy)).toBe(true);
    });

    test("a file without the external banner counts as customized", () => {
        expect(isExternalMfaHookDefault("mfaLoginEnabled = False\n")).toBe(false);
        expect(
            isExternalMfaHookDefault(
                SHIPPED_HOOK.replace("ADD CUSTOM EXTERNAL OAUTH IDP LOGIC", "REMOVED")
            )
        ).toBe(false);
    });

    test("acknowledging the ID on the app removes the warning", () => {
        const stack = synth(externalOAuth("mfa"));
        cdk.Annotations.of(stack.node.root).acknowledgeWarning(EXTERNAL_OAUTH_MFA_HOOK_WARNING_ID);
        expect(hookWarnings(stack)).toEqual([]);
    });
});
