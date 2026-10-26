/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { fetchAuthSession, TokenProvider, decodeJWT } from "aws-amplify/auth";
import { OAuth2Token, OAuth2Client } from "@badgateway/oauth2-client";
import { appCache } from "../services/appCache";

/**
 * Reference to the OAuth2Client instance from Auth.tsx
 * This will be set by Auth.tsx during initialization
 */
let oauth2ClientInstance: OAuth2Client | null = null;

/**
 * Sets the OAuth2Client instance for use by token utilities
 * Called by Auth.tsx during initialization
 */
export function setOAuth2ClientInstance(client: OAuth2Client): void {
    oauth2ClientInstance = client;
}

/**
 * Gets the OAuth2Client instance
 * Throws error if not initialized (should only happen in OAuth2 mode)
 */
function getOAuth2ClientInstance(): OAuth2Client {
    if (!oauth2ClientInstance) {
        throw new Error("OAuth2Client not initialized. This should only be called in OAuth2 mode.");
    }
    return oauth2ClientInstance;
}

/**
 * Gets OAuth2 token from localStorage (External OAuth2 only)
 * Returns empty object if token doesn't exist or is invalid
 */
export function getExternalOAuth2Token(): OAuth2Token {
    let oauth2Token = {} as OAuth2Token;
    const oauth2TokenStr = localStorage.getItem("oauth2_token");
    if (oauth2TokenStr) {
        try {
            oauth2Token = JSON.parse(oauth2TokenStr);
        } catch (error) {
            console.error("Error parsing OAuth2 token:", error);
        }
    }
    return oauth2Token;
}

/**
 * Validates if access and refresh tokens are still valid (External OAuth2 only)
 * Returns [accessTokenValid, refreshTokenValid]
 */
export function externalTokenValidation(): [boolean, boolean] {
    let accessTokenValid = false;
    let refreshTokenValid = false;
    const oauth2Token = getExternalOAuth2Token();

    // If access token exists and not expired, deem it as still valid
    if (
        oauth2Token.accessToken &&
        oauth2Token.accessToken.length > 0 &&
        oauth2Token.expiresAt &&
        Date.now() < oauth2Token.expiresAt
    ) {
        accessTokenValid = true;
    }
    // If access token expired and refresh token exists, deem it as still valid
    else if (oauth2Token.refreshToken) {
        refreshTokenValid = true;
    }

    return [accessTokenValid, refreshTokenValid];
}

/**
 * Parses JWT token to extract payload
 */
const parseJwt = (accessToken: string): Record<string, unknown> => {
    let jsonPayload = "{}";
    const base64Url = accessToken.split(".")[1];
    if (base64Url) {
        const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
        jsonPayload = decodeURIComponent(
            window
                .atob(base64)
                .split("")
                .map(function (c) {
                    return "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2);
                })
                .join("")
        );
    }

    return JSON.parse(jsonPayload);
};

/**
 * Claims the user id is read from when `externalOAuthIdpUserIdClaim` is not set, in order.
 */
const EXTERNAL_USER_ID_CLAIM_CHAIN = ["cognito:username", "username", "sub"];

/**
 * The access-token claim configured as the VAMS user id (`externalOAuthIdpUserIdClaim` in the
 * cached amplify-config, from `useExternalOAuthIdp.lambdaAuthorizorJWTUserIdClaim`), or null when
 * it is not set: absent from the cached config, empty, or the `"undefined"` placeholder
 * amplify-config uses for an unset field.
 *
 * Read from the cache rather than passed in, so the sign-in code exchange and the token refresh
 * (which has no config at hand) apply the same claim.
 */
export function getExternalUserIdClaim(): string | null {
    const value = appCache.getItem("config")?.externalOAuthIdpUserIdClaim;
    if (typeof value !== "string" || value.trim() === "" || value === "undefined") {
        return null;
    }
    return value;
}

/**
 * The VAMS user id in an external OAuth access token's claims.
 *
 * Set (`userIdClaim`): that claim only, "" when the token lacks it (the API refuses such a token,
 * so there is nothing to fall back to). Not set: the first of cognito:username, username and sub
 * that is a non-empty string.
 *
 * Keep this in step with resolve_user_id in backend/backend/common/auth/authorizerCore.py, which
 * applies the same rule in the API authorizer: the login profile request is keyed on this value
 * and is refused unless it is the user id the authorizer resolved. String claims give the same
 * result on both sides; for a non-string value the authorizer takes the first truthy claim of the
 * chain and this function the first non-empty string.
 */
export function resolveExternalUserId(
    claims: Record<string, unknown>,
    userIdClaim: string | null
): string {
    if (userIdClaim) {
        const value = claims[userIdClaim];
        return typeof value === "string" ? value : "";
    }
    for (const name of EXTERNAL_USER_ID_CLAIM_CHAIN) {
        const value = claims[name];
        if (typeof value === "string" && value !== "") {
            return value;
        }
    }
    return "";
}

/**
 * localStorage key naming the user id claim an external OAuth access token lacked. The sign-in
 * page shows the claim, and sessionManager.logoutExpired keeps it instead of reporting an
 * expired session.
 */
export const MISSING_USER_ID_CLAIM_KEY = "auth_missing_user_id_claim";

/**
 * Thrown for an external OAuth access token without the configured user id claim (absent, empty
 * or not a string). The API refuses such a token on every request, so no session is opened for
 * it. Carries the claim name only, never the token or a claim value.
 */
export class MissingUserIdClaimError extends Error {
    readonly claim: string;

    constructor(claim: string) {
        super(`The access token has no "${claim}" claim to identify the user.`);
        this.name = "MissingUserIdClaimError";
        this.claim = claim;
    }
}

/**
 * The user id for an external OAuth access token under the cached config's user id claim.
 * Throws MissingUserIdClaimError when the claim is set and the token lacks it.
 */
function externalOAuthUsername(accessToken: string): string {
    const userIdClaim = getExternalUserIdClaim();
    const username = resolveExternalUserId(parseJwt(accessToken), userIdClaim);
    if (userIdClaim && !username) {
        throw new MissingUserIdClaimError(userIdClaim);
    }
    return username;
}

/**
 * Sets OAuth2 token in localStorage and updates Amplify session (External OAuth2 only)
 * This function is used internally and by Auth.tsx
 *
 * Throws MissingUserIdClaimError, storing nothing, for a token without the configured user id
 * claim.
 */
export function setExternalOauth2Token(oauth2Token: OAuth2Token): void {
    const username = externalOAuthUsername(oauth2Token.accessToken);

    localStorage.setItem("oauth2_token", JSON.stringify(oauth2Token));
    localStorage.setItem("user", JSON.stringify({ username }));

    // Removed: AmplifyAuth.setUserSession() — not needed with custom apiClient + TokenProvider
}

/**
 * Re-derives the stored user from the stored access token with the current user id claim
 * (External OAuth2 only). Auth.tsx calls it on page load and after a refreshed amplify-config
 * lands, so the stored user is the id the API resolves with the claim the backend names. Returns
 * true when the stored user changed; throws MissingUserIdClaimError when the token lacks the claim.
 */
export function syncExternalOAuthUser(): boolean {
    const accessToken = getExternalOAuth2Token().accessToken;
    if (!accessToken) {
        return false;
    }
    const before = localStorage.getItem("user");
    localStorage.setItem("user", JSON.stringify({ username: externalOAuthUsername(accessToken) }));
    return localStorage.getItem("user") !== before;
}

/**
 * Coalesces concurrent OAuth2 refreshes onto a single in-flight promise. Several API
 * calls firing in parallel after the access token expires would otherwise each issue
 * their own refreshToken() with the same refresh token; IdPs that rotate refresh tokens
 * invalidate it after the first use, so the rest fail and force a spurious re-login.
 *
 * A refreshed token without the configured user id claim ends the session: the stored token and
 * user are removed and the claim is recorded under MISSING_USER_ID_CLAIM_KEY, so every caller
 * finds no session and the sign-out names the claim.
 */
let oauth2RefreshInFlight: Promise<string> | null = null;

async function refreshOAuth2AccessToken(): Promise<string> {
    if (oauth2RefreshInFlight) {
        return oauth2RefreshInFlight;
    }
    oauth2RefreshInFlight = (async () => {
        const oauth2Client = getOAuth2ClientInstance();
        const currentToken = getExternalOAuth2Token();
        const newToken = await oauth2Client.refreshToken(currentToken);
        try {
            setExternalOauth2Token(newToken);
        } catch (error) {
            if (error instanceof MissingUserIdClaimError) {
                localStorage.removeItem("oauth2_token");
                localStorage.removeItem("user");
                localStorage.setItem(MISSING_USER_ID_CLAIM_KEY, error.claim);
            }
            throw error;
        }
        console.log("OAuth2 token refreshed successfully");
        return newToken.accessToken;
    })().finally(() => {
        oauth2RefreshInFlight = null;
    });
    return oauth2RefreshInFlight;
}

/**
 * Gets a valid, fresh access token for API calls (Works with both Cognito and OAuth2)
 * Handles both Cognito and OAuth2 modes
 * Automatically refreshes expired tokens when possible
 *
 * @returns Promise<string> - A valid access token
 * @throws Error if unable to get or refresh token
 */
export async function getDualValidAccessToken(): Promise<string> {
    if (window.DISABLE_COGNITO) {
        // OAuth2 Mode
        const [accessTokenValid, refreshTokenValid] = externalTokenValidation();

        if (accessTokenValid) {
            // Access token is still valid, return it
            return getExternalOAuth2Token().accessToken;
        }

        if (refreshTokenValid) {
            // Access token expired but refresh token exists, attempt to refresh.
            // Coalesced so parallel callers share one refresh (see refreshOAuth2AccessToken).
            try {
                return await refreshOAuth2AccessToken();
            } catch (error) {
                console.error("Failed to refresh OAuth2 token:", error);
                throw new Error("Failed to refresh OAuth2 token. Please log in again.");
            }
        }

        throw new Error("No valid OAuth2 token available. Please log in again.");
    } else {
        // Cognito Mode
        try {
            const session = await fetchAuthSession();
            const token = session.tokens?.idToken?.toString();
            // Throw on an absent token rather than returning "" — an empty Bearer header
            // would otherwise be sent, deferring detection of a dead session by a failed
            // round-trip. Mirrors the OAuth2 branch, which throws on absence.
            if (!token) {
                throw new Error("No valid Cognito token available. Please log in again.");
            }
            return token;
        } catch (error) {
            console.error("Failed to get Cognito session:", error);
            throw new Error("Failed to get valid Cognito token. Please log in again.");
        }
    }
}

/**
 * Gets a valid access token for use in Authorization headers (Works with both Cognito and OAuth2)
 * Convenience wrapper around getDualValidAccessToken()
 *
 * @returns Promise<string> - Bearer token string ready for Authorization header
 */
export async function getDualAuthorizationHeader(): Promise<string> {
    const token = await getDualValidAccessToken();
    return `Bearer ${token}`;
}

/**
 * Epoch-ms expiry of the token currently used for API auth, or null if unknown.
 * OAuth2: the stored token's expiresAt. Cognito: the idToken's exp claim (the
 * idToken is what VAMS sends as the Bearer token).
 */
export async function getCurrentTokenExpiryMs(): Promise<number | null> {
    if (window.DISABLE_COGNITO) {
        const oauth2Token = getExternalOAuth2Token();
        return typeof oauth2Token.expiresAt === "number" ? oauth2Token.expiresAt : null;
    }
    try {
        const session = await fetchAuthSession();
        const exp = session.tokens?.idToken?.payload?.exp;
        return typeof exp === "number" ? exp * 1000 : null;
    } catch {
        return null;
    }
}

/**
 * Validate the active session, refreshing when needed (or when forceRefresh is set).
 * Returns true if a usable token exists afterward, false if the session is dead.
 * Never throws — callers treat false as "session unrecoverable".
 */
export async function ensureSessionValid(forceRefresh = false): Promise<boolean> {
    if (window.DISABLE_COGNITO) {
        const [accessTokenValid, refreshTokenValid] = externalTokenValidation();
        if (accessTokenValid && !forceRefresh) {
            return true;
        }
        if (refreshTokenValid || (accessTokenValid && forceRefresh)) {
            try {
                // Share the coalesced refresh (see refreshOAuth2AccessToken) rather than
                // calling the client directly. Focus revalidation and the API layer both
                // reach this function, and an IdP that rotates refresh tokens rejects the
                // second request made with the same one.
                await refreshOAuth2AccessToken();
                return true;
            } catch (error) {
                console.error("Failed to refresh OAuth2 token:", error);
                // The refreshed token lacks the configured user id claim: the refresh already
                // ended the session (see refreshOAuth2AccessToken), even while the access token
                // in hand is still valid.
                if (error instanceof MissingUserIdClaimError) {
                    return false;
                }
                // A failed refresh does not by itself end the session: the proactive refresh runs
                // while the access token is still valid (a minute before expiry, and again on the
                // one-hour timer cap), so a transient IDP failure here leaves a token that still
                // authenticates every request. Reporting the access token's own validity keeps the
                // user working and lets the re-armed timer retry; once the token really expires
                // this is false and the caller signs them out.
                return accessTokenValid;
            }
        }
        return accessTokenValid;
    }

    try {
        const session = await fetchAuthSession(forceRefresh ? { forceRefresh: true } : undefined);
        return !!session.tokens?.idToken;
    } catch (error) {
        console.error("Failed to validate Cognito session:", error);
        return false;
    }
}

/**
 * Custom TokenProvider for external OAuth2 mode.
 * Bridges @badgateway/oauth2-client tokens to Amplify v6's auth system.
 * Used in Amplify.configure() when DISABLE_COGNITO is true.
 */
export const externalOAuthTokenProvider: TokenProvider = {
    async getTokens({ forceRefresh } = {}) {
        const oauth2TokenStr = localStorage.getItem("oauth2_token");
        if (!oauth2TokenStr) return null;

        let oauth2Token;
        try {
            oauth2Token = JSON.parse(oauth2TokenStr);
        } catch {
            return null;
        }

        if (forceRefresh && oauth2Token.refreshToken && oauth2ClientInstance) {
            try {
                // Coalesced with every other refresh in the process — see
                // refreshOAuth2AccessToken. It persists the new token itself.
                const accessToken = await refreshOAuth2AccessToken();
                return {
                    accessToken: decodeJWT(accessToken),
                    idToken: decodeJWT(accessToken),
                };
            } catch (error) {
                console.error("Token refresh failed:", error);
                return null;
            }
        }

        if (!oauth2Token.accessToken) return null;

        return {
            accessToken: decodeJWT(oauth2Token.accessToken),
            idToken: decodeJWT(oauth2Token.accessToken),
        };
    },
};
