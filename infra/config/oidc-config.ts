/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cognito from "aws-cdk-lib/aws-cognito";

/**
 * Settings for federating a Cognito user pool to an external OIDC identity
 * provider (Amazon Midway via Amazon Federate).
 *
 * This mirrors the pattern of `saml-config.ts`: the enable flag lives here as a
 * standalone constant (like `useCognito.useSaml` does for SAML), and the settings
 * object carries the provider details. When enabled, the Cognito construct adds a
 * `UserPoolIdentityProviderOidc` to the user pool and the web UI shows a
 * "Login with Amazon Midway" button alongside the native username/password form.
 */
export interface OidcSettings {
    // Provider name registered in the Cognito user pool. The web UI federates
    // against this exact name (cognitoFederatedConfig.customFederatedIdentityProviderName).
    name: string;
    // Cognito hosted-UI domain prefix. Full domain becomes
    // https://<prefix>.auth.<region>.amazoncognito.com
    cognitoDomainPrefix: string;
    // OIDC client credentials issued by Amazon Federate for this app.
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
    // Whether CDK should create/manage the Cognito hosted domain. Set false when
    // the domain was created out-of-band (e.g. manually in the console) to avoid
    // a "domain already exists" conflict on deploy.
    manageDomain: boolean;
}

/**
 * Toggle Cognito <-> OIDC (Amazon Midway / Amazon Federate) federation.
 * Set to false to fall back to Cognito-only (native username/password) login.
 */
export const useOidcFederation = true;

export const oidcSettings: OidcSettings = {
    name: "AmazonMidway",
    cognitoDomainPrefix: "vams",
    clientId: "vams-midway-client",
    // The client secret is retrieved from AWS Secrets Manager.
    // Before deploying, create the secret:
    //   aws secretsmanager create-secret \
    //     --name vams/oidc/midway-client-secret \
    //     --secret-string "YOUR_CLIENT_SECRET_HERE" \
    //     --region YOUR_REGION
    clientSecretArn: "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:vams/oidc/midway-client-secret",
    // Amazon Federate INTEG environment issuer (from the .well-known endpoint you registered).
    issuerUrl: "https://idp-integ.federate.amazon.com",
    scopes: ["openid", "email", "profile"],
    attributeMapping: {
        email: cognito.ProviderAttribute.other("email"),
    },
    // The 'vams' hosted domain was created manually on the pool, so CDK must not recreate it.
    manageDomain: false,
};
