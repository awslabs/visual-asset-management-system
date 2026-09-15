/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cognito from "aws-cdk-lib/aws-cognito";

/**
 * Settings for federating a Cognito user pool to an external OIDC identity
 * provider.
 *
 * This mirrors the pattern of `saml-config.ts`: the enable flag lives here as a
 * standalone constant (like `useCognito.useSaml` does for SAML), and the settings
 * object carries the provider details. When enabled, the Cognito construct adds a
 * `UserPoolIdentityProviderOidc` to the user pool and the web UI shows a
 * federated login button alongside the native username/password form.
 */
export interface OidcSettings {
    // Provider name registered in the Cognito user pool. The web UI federates
    // against this exact name (cognitoFederatedConfig.customFederatedIdentityProviderName).
    name: string;
    // Display name shown on the login button (e.g., "Login with <displayName>")
    displayName: string;
    // Cognito hosted-UI domain prefix. Full domain becomes
    // https://<prefix>.auth.<region>.amazoncognito.com
    cognitoDomainPrefix: string;
    // OIDC client credentials issued by your identity provider for this app.
    clientId: string;
    // Client secret is loaded from AWS Secrets Manager at deploy time.
    // The secret ARN or name is specified here, NOT the plaintext value.
    clientSecretArn: string;
    // OIDC issuer base URL. Cognito auto-discovers the authorize/token/jwks
    // endpoints from <issuerUrl>/.well-known/openid-configuration.
    issuerUrl: string;
    // OIDC scopes to request.
    scopes: string[];
    // Map incoming OIDC claims to Cognito user attributes.
    attributeMapping: cognito.AttributeMapping;
    // Whether CDK creates and manages the Cognito hosted domain. True for a normal deployment.
    // Set false only when the domain already exists on the user pool, which CloudFormation
    // cannot adopt — leaving it true in that case fails the deploy with "domain already exists".
    manageDomain: boolean;
}

/**
 * OIDC federation is toggled via the main config: app.authProvider.useCognito.useOidc.
 * When enabled, the Cognito construct uses the oidcSettings below to add an OIDC
 * identity provider to the user pool.
 */

export const oidcSettings: OidcSettings = {
    name: "ExternalOIDC",
    displayName: "SSO",
    cognitoDomainPrefix: "vams",
    clientId: "vams-oidc-client",
    // The client secret is retrieved from AWS Secrets Manager.
    // Before deploying, create the secret:
    //   aws secretsmanager create-secret \
    //     --name vams/oidc/client-secret \
    //     --secret-string "YOUR_CLIENT_SECRET_HERE" \
    //     --region YOUR_REGION
    clientSecretArn: "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:vams/oidc/client-secret",
    // OIDC provider issuer URL (from the .well-known endpoint you registered).
    issuerUrl: "https://your-idp.example.com",
    scopes: ["openid", "email", "profile"],
    attributeMapping: {
        email: cognito.ProviderAttribute.other("email"),
    },
    // Let CDK create the hosted domain. Set this to false ONLY when the domain already exists on
    // the user pool (created out-of-band), because CloudFormation cannot adopt an existing domain
    // and the deploy fails with a "domain already exists" error.
    manageDomain: true,
};
