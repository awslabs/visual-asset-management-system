/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** Shown when a deployment configures no display name for its identity provider. */
export const DEFAULT_IDP_LABEL = "SSO";

/**
 * The label for a federated login button: "Log in with {this}".
 *
 * The value reaches the frontend through `/api/amplify-config`, which renders an unset field as the
 * **string** `"undefined"` rather than omitting it, so that spelling has to be treated as absent —
 * the button would otherwise read "Log in with undefined". Blank and whitespace-only values fall
 * back for the same reason.
 *
 * Used for both federated paths (an Amazon Cognito user pool federated to SAML or OIDC, and an
 * external OAuth provider) so the two cannot drift apart.
 */
export function idpButtonLabel(displayName?: string | null): string {
    if (!displayName || displayName === "undefined" || displayName.trim() === "") {
        return DEFAULT_IDP_LABEL;
    }
    return displayName.trim();
}
