/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Cognito web client must not carry permissive OAuth settings it does not need.
 *
 * Omitting `oAuth` on a `UserPoolClient` does NOT mean "no OAuth". CDK applies its own defaults:
 * the IMPLICIT grant alongside the authorization-code grant, five scopes including
 * `aws.cognito.signin.user.admin`, and `https://example.com` as the callback URL — a domain the
 * operator does not control. That was measured on the live non-federated development deployment,
 * whose client reported exactly:
 *
 *     flows:     ["code", "implicit"]
 *     scopes:    ["aws.cognito.signin.user.admin", "email", "openid", "phone", "profile"]
 *     callbacks: ["https://example.com"]
 *
 * Two facts decide the fix, and both are asserted here rather than assumed:
 *
 *  1. Without federation VAMS uses no OAuth flow at all — Amplify signs in with SRP (or
 *     USER_PASSWORD) and the identity pool exchanges the resulting token — and no user pool domain is
 *     created, because `addDomain` is called only on the SAML path. So there is no
 *     `/oauth2/authorize` endpoint and the permissive settings are latent rather than reachable.
 *     `disableOAuth` removes the surface instead of relying on that.
 *  2. WITH federation the settings are deliberately left alone. `CustomCognitoConfigConstruct` in the
 *     static-web stack narrows them post-deploy and supplies the real callback URLs, which this stack
 *     cannot know. Declaring them here would have CloudFormation overwrite that hardening on its next
 *     update to the client — the custom resource is keyed on its own inputs and would not re-run — so
 *     the federated half of S1-INFRA-022 stays open and needs a federated environment to close.
 *
 * The federated case is asserted as "unchanged" on purpose. A test that only checked the hardened
 * branch would pass just as well after someone applied the same settings to both, which is the change
 * that breaks federated sign-in.
 */

import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";
import { cognitoWebClientUpdateParameters } from "../../lib/nestedStacks/auth/constructs/cognito-web-native-construct";

/** Every shipped configuration is non-federated, so all three take the hardened branch. */
const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];

/** The web client, identified by the client name the construct sets. */
function webClients(synth: SynthResult) {
    return synth
        .ofType("AWS::Cognito::UserPoolClient")
        .filter((c) => /web/i.test(String(SynthResult.flatten(c.properties.ClientName ?? ""))));
}

describe.each(TEMPLATES)("%s: Cognito web client OAuth", (templateName) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate(templateName);
    });

    test("a web client exists to make the assertions below meaningful", () => {
        // The positive control. Every "does not carry X" assertion below is satisfied by a template
        // with no client at all, which is what this rules out.
        const clients = webClients(synth);
        expect(clients.length).toBeGreaterThan(0);
    });

    test("OAuth is disabled, so no flow or scope is enabled", () => {
        for (const client of webClients(synth)) {
            expect(client.properties.AllowedOAuthFlowsUserPoolClient).toBe(false);
        }
    });

    test("the implicit grant is absent", () => {
        // Named separately from the blanket check because this is the security-relevant half: an
        // implicit-grant token is delivered in a redirect fragment to whatever callback is registered.
        for (const client of webClients(synth)) {
            const flows = client.properties.AllowedOAuthFlows ?? [];
            expect(flows).not.toContain("implicit");
        }
    });

    test("the aws.cognito.signin.user.admin scope is absent", () => {
        // That scope authorizes user pool self-service API calls with the issued token.
        for (const client of webClients(synth)) {
            const scopes = client.properties.AllowedOAuthScopes ?? [];
            expect(scopes).not.toContain("aws.cognito.signin.user.admin");
        }
    });

    test("no callback URL points at a domain the deployment does not own", () => {
        // CDK's default is literally https://example.com.
        for (const client of webClients(synth)) {
            const callbacks = (client.properties.CallbackURLs ?? []).map((c: unknown) =>
                SynthResult.flatten(c)
            );
            expect(callbacks.join(" ")).not.toContain("example.com");
        }
    });

    test("username enumeration is prevented", () => {
        // Left unset, Cognito applies LEGACY and answers an unknown username with
        // UserNotFoundException on sign-in AND on password recovery, which confirms whether an account
        // exists. Asserted as the emitted ENABLED rather than on the CDK prop, because the two are
        // different spellings and only the emitted one reaches Cognito.
        for (const client of webClients(synth)) {
            expect(client.properties.PreventUserExistenceErrors).toBe("ENABLED");
        }
    });

    test("the federated repair carries the setting too", () => {
        // UpdateUserPoolClient is a full replace, so a property the repair's parameter set omits reverts
        // to its Cognito default - and this one's default is the enumerating behaviour. Asserted on the
        // shared parameter builder, which is what the custom resource spreads.
        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../lib/nestedStacks/auth/constructs/cognito-web-native-construct.ts"
            ),
            "utf-8"
        );
        const params = source.slice(
            source.indexOf("export function cognitoWebClientUpdateParameters"),
            source.indexOf("Deploys Cognito with an Authenticated")
        );
        expect(params.length).toBeGreaterThan(0);
        expect(params).toContain('PreventUserExistenceErrors: "ENABLED"');
    });

    test("the authentication flows and token lifetimes are untouched", () => {
        // Disabling OAuth must not have disturbed how sign-in actually works. SRP is what Amplify uses.
        for (const client of webClients(synth)) {
            expect(client.properties.ExplicitAuthFlows).toContain("ALLOW_USER_SRP_AUTH");
            expect(client.properties.ExplicitAuthFlows).toContain("ALLOW_REFRESH_TOKEN_AUTH");
        }
    });
});

describe("the federated branch is deliberately left to the custom resource", () => {
    /**
     * Built directly rather than through a config template, because no shipped template enables
     * federation — `useSaml`/`useOidc` are commercial-only and off by default.
     */
    function synthWithSaml() {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const Config = require("../../config/config");
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const commercial = require("../../config/config.template.commercial.json");
        const config = JSON.parse(JSON.stringify(commercial));
        config.env.region = "us-west-2";
        config.env.account = "123456789012";
        config.app.baseStackName = "vamstest";
        config.app.authProvider.useCognito.useSaml = true;
        void Config;
        return config;
    }

    test("enabling SAML keeps disableOAuth OFF, so the custom resource stays authoritative", () => {
        // The guard against "harden both branches", which would make CloudFormation overwrite the
        // callback URLs the custom resource sets and break federated sign-in with no error.
        const config = synthWithSaml();
        expect(
            config.app.authProvider.useCognito.useSaml || config.app.authProvider.useCognito.useOidc
        ).toBe(true);

        // Asserted on the construct's own branch condition rather than on a full synth: building the
        // auth stack with SAML needs provider metadata this test has no business inventing, and the
        // branch is the whole behaviour under test.
        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../lib/nestedStacks/auth/constructs/cognito-web-native-construct.ts"
            ),
            "utf-8"
        );
        expect(source).toContain("...(federationEnabled ? {} : { disableOAuth: true })");
        expect(source).toMatch(/useCognito\.useSaml\s*\|\|/);
        expect(source).toMatch(/useCognito\.useOidc/);
    });
});

describe("no user pool domain exists without federation", () => {
    // The fact the "latent rather than reachable" reasoning rests on. If a domain ever appeared in a
    // non-federated deployment, the hosted UI would become reachable and the OAuth settings would
    // stop being inert — so this is asserted rather than left as a comment.
    test.each(TEMPLATES)("%s emits no UserPoolDomain", (templateName) => {
        const synth = synthTemplate(templateName);
        expect(synth.ofType("AWS::Cognito::UserPoolDomain")).toHaveLength(0);
    });
});

// Keeps the CDK import used, and documents which library version the defaults above were observed on.
void cdk;
void Template;

describe("the custom authentication flow is not enabled", () => {
    /**
     * ALLOW_CUSTOM_AUTH requires DefineAuthChallenge, CreateAuthChallenge and VerifyAuthChallengeResponse
     * triggers on the user pool. VAMS declares none — its only pool trigger is pre-token-generation — so
     * the flow can never complete and exists purely as reachable surface.
     *
     * Both places are asserted. `cognitoWebClientUpdateParameters` mirrors the flow list for the federated
     * repair custom resource, and UpdateUserPoolClient is a FULL replace: a list that still names
     * ALLOW_CUSTOM_AUTH would put the flow back on every post-deploy update, so removing it from the
     * construct alone is not enough.
     */
    const synth = synthTemplate("commercial");

    test("no web client enables ALLOW_CUSTOM_AUTH", () => {
        const clients = webClients(synth);
        // Control: clients exist and their flow list is populated, so the absence below is meaningful.
        expect(clients.length).toBeGreaterThan(0);
        for (const client of clients) {
            expect(client.properties.ExplicitAuthFlows?.length ?? 0).toBeGreaterThan(0);
            expect(client.properties.ExplicitAuthFlows).not.toContain("ALLOW_CUSTOM_AUTH");
        }
    });

    test("the sign-in flows the product actually uses are still enabled", () => {
        // The positive control for the removal: SRP is what Amplify uses in the browser.
        for (const client of webClients(synth)) {
            expect(client.properties.ExplicitAuthFlows).toContain("ALLOW_USER_SRP_AUTH");
            expect(client.properties.ExplicitAuthFlows).toContain("ALLOW_REFRESH_TOKEN_AUTH");
        }
    });

    test("the update-parameter mirror omits it too", () => {
        // A minimal stand-in rather than a full Config: the function reads only these two fields, so
        // building the whole object would add coupling without adding coverage.
        const stub = {
            app: {
                authProvider: {
                    useCognito: { credTokenTimeoutSeconds: 3600, useUserPasswordAuthFlow: false },
                },
            },
        } as unknown as Parameters<typeof cognitoWebClientUpdateParameters>[0];

        const params = cognitoWebClientUpdateParameters(stub);
        const flows = params.ExplicitAuthFlows as string[];
        expect(flows).not.toContain("ALLOW_CUSTOM_AUTH");
        expect(flows).toContain("ALLOW_USER_SRP_AUTH");
        expect(flows).toContain("ALLOW_REFRESH_TOKEN_AUTH");
    });

    test("the user pool declares no custom-auth challenge trigger", () => {
        // The premise of the removal. If a trigger is ever added, this fails and the flow should be
        // re-enabled rather than the test relaxed.
        for (const pool of synth.ofType("AWS::Cognito::UserPool")) {
            const lambdaConfig = (pool.properties.LambdaConfig ?? {}) as Record<string, unknown>;
            expect(lambdaConfig).not.toHaveProperty("DefineAuthChallenge");
            expect(lambdaConfig).not.toHaveProperty("CreateAuthChallenge");
            expect(lambdaConfig).not.toHaveProperty("VerifyAuthChallengeResponse");
        }
    });
});
