/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every attribute in the synthesized user pool schema must carry a name Amazon Cognito accepts.
 *
 * A custom attribute is declared by its bare name. Cognito prefixes `custom:` itself, and it rejects a
 * name outside `_|ALPHA *(nameChar)` with `nameChar = "$" / "-" / "_" / DIGIT / ALPHA` (400
 * InvalidRequest, "Attribute names MUST conform to the following rules"). A key written with the prefix
 * (`custom:groups`) contains a colon, so a new pool fails to create, the AuthBuilder nested stack fails,
 * and the whole core stack rolls back.
 *
 * The second block pins the pool's sign-up, MFA, password, threat-protection and app-client settings
 * and the identity pool's unauthenticated access to the values the app emits, so a schema change cannot
 * move any of them unnoticed.
 */

import { Resource, SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];

/** Cognito's custom attribute name rule, as its rejection message states it. */
const COGNITO_ATTRIBUTE_NAME = /^[A-Za-z_][A-Za-z0-9$_-]*$/;

/** Threat protection is commercial-only: the restricted partitions emit neither property. */
const THREAT_PROTECTION: Record<TemplateName, { addOns?: unknown; tier?: unknown }> = {
    commercial: { addOns: { AdvancedSecurityMode: "ENFORCED" }, tier: "PLUS" },
    govcloud: {},
    eusovereign: {},
};

function userPools(synth: SynthResult): Resource[] {
    return synth.ofType("AWS::Cognito::UserPool");
}

describe.each(TEMPLATES)("%s: Cognito user pool schema", (templateName) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate(templateName);
    });

    test("a user pool exists to make the name check meaningful", () => {
        expect(userPools(synth).length).toBeGreaterThan(0);
    });

    test("every schema attribute name is one Cognito accepts", () => {
        const rejected = userPools(synth).flatMap((pool) =>
            ((pool.properties.Schema ?? []) as Array<{ Name?: unknown }>)
                .map((attr) => String(attr.Name))
                .filter((attrName) => !COGNITO_ATTRIBUTE_NAME.test(attrName))
                .map((attrName) => `${pool.stack}/${pool.logicalId}: ${attrName}`)
        );
        expect(rejected).toEqual([]);
    });
});

describe.each(TEMPLATES)("%s: Cognito authentication settings", (templateName) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate(templateName);
    });

    test("the user pool's sign-up, MFA, password and threat-protection settings", () => {
        const pools = userPools(synth);
        expect(pools.length).toBe(1);
        const props = pools[0].properties;

        expect(props.AdminCreateUserConfig.AllowAdminCreateUserOnly).toBe(true);
        expect(props.MfaConfiguration).toBe("OPTIONAL");
        expect(props.EnabledMfas).toEqual(["SMS_MFA", "SOFTWARE_TOKEN_MFA"]);
        expect(props.Policies).toEqual({
            PasswordPolicy: {
                MinimumLength: 8,
                RequireLowercase: true,
                RequireNumbers: true,
                RequireSymbols: true,
                RequireUppercase: true,
                TemporaryPasswordValidityDays: 3,
            },
        });
        expect(props.UserPoolAddOns).toEqual(THREAT_PROTECTION[templateName].addOns);
        expect(props.UserPoolTier).toEqual(THREAT_PROTECTION[templateName].tier);
    });

    test("the web client's authentication flows and token lifetimes", () => {
        const clients = synth.ofType("AWS::Cognito::UserPoolClient");
        expect(clients.length).toBe(1);
        const props = clients[0].properties;

        expect(props.GenerateSecret).toBe(false);
        expect(props.ExplicitAuthFlows).toEqual([
            "ALLOW_USER_SRP_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
        ]);
        expect(props.AccessTokenValidity).toBe(60);
        expect(props.IdTokenValidity).toBe(60);
        expect(props.RefreshTokenValidity).toBe(1440);
        expect(props.TokenValidityUnits).toEqual({
            AccessToken: "minutes",
            IdToken: "minutes",
            RefreshToken: "minutes",
        });
        expect(props.PreventUserExistenceErrors).toBe("ENABLED");
    });

    test("the identity pool refuses unauthenticated identities", () => {
        const identityPools = synth.ofType("AWS::Cognito::IdentityPool");
        expect(identityPools.length).toBe(1);
        expect(identityPools[0].properties.AllowUnauthenticatedIdentities).toBe(false);
    });
});
