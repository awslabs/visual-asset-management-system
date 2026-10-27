/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The external OAuth MFA scope setting shows a login button and appends a scope; it does not by
 * itself make MFA-required roles work, because the shipped external branch of the MFA hook reports
 * no MFA. The configuration reference row and the login step of the user guide say so.
 */

import { page, read } from "./support/docsText";

const ROW_KEY = "`app.authProvider.useExternalOAuthIdp.idpAuthProviderScopeMfa`";

const row = (): string =>
    page("deployment/configuration-reference.md")
        .split("\n")
        .find((line) => line.startsWith(`| ${ROW_KEY}`)) ?? "";

describe("external OAuth MFA scope documentation", () => {
    test("the configuration reference has the idpAuthProviderScopeMfa row (control)", () => {
        expect(row()).not.toBe("");
    });

    test("the external hook branch the docs point at still ships reporting no MFA", () => {
        const hook = read("backend/backend/customConfigCommon/customAuthClaimsCheck.py");
        expect(hook).toMatch(/#External OAUTH IDP MFA check\s*\n\s*mfaLoginEnabled = False/);
    });

    // TEMPORARY-TEST: pins the rewording of the idpAuthProviderScopeMfa row and the external OAuth login step
    test("the row and the login step name the hook instead of promising enforcement", () => {
        expect(row()).not.toContain("Set to enable MFA enforcement");
        expect(row()).toContain("customMFATokenScopeCheckOverride");
        expect(row()).toContain("../developer/security.md#authentication-override-hooks");
        const loginStep = page("user-guide/getting-started.mdx");
        expect(loginStep).not.toContain("if multi-factor authentication is required");
        expect(loginStep).toContain("../developer/security.md#authentication-override-hooks");
    });
});
