/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { fetchAuthSession, TokenProvider, decodeJWT } from "aws-amplify/auth";
import { OAuth2Token, OAuth2Client } from "@badgateway/oauth2-client";

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
const parseJwt = (
    accessToken: string
): {
    sub: string;
} => {
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
 * Sets OAuth2 token in localStorage and updates Amplify session (External OAuth2 only)
 * This function is used internally and by Auth.tsx
 */
export function setExternalOauth2Token(oauth2Token: OAuth2Token): void {
    localStorage.setItem("oauth2_token", JSON.stringify(oauth2Token));

    const jwt = parseJwt(oauth2Token.accessToken);
    localStorage.setItem("user", JSON.stringify({ username: jwt.sub }));

    // Removed: AmplifyAuth.setUserSession() — not needed with custom apiClient + TokenProvider
}

/**
 * Coalesces concurrent OAuth2 refreshes onto a single in-flight promise. Several API
 * calls firing in parallel after the access token expires would otherwise each issue
 * their own refreshToken() with the same refresh token; IdPs that rotate refresh tokens
 * invalidate it after the first use, so the rest fail and force a spurious re-login.
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
        setExternalOauth2Token(newToken);
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
                const client = getOAuth2ClientInstance();
                const newToken = await client.refreshToken(getExternalOAuth2Token());
                setExternalOauth2Token(newToken);
                return true;
            } catch (error) {
                console.error("Failed to refresh OAuth2 token:", error);
                return false;
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
                const newToken = await oauth2ClientInstance.refreshToken(oauth2Token);
                localStorage.setItem("oauth2_token", JSON.stringify(newToken));
                return {
                    accessToken: decodeJWT(newToken.accessToken),
                    idToken: decodeJWT(newToken.accessToken),
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
