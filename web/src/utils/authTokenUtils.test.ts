/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Guards FIX-030 (S5-WEB-030) in part: moving the external-OAuth tokens out of localStorage is
 * deferred (docs/review/DEFERRED.md). What is asserted here is the refresh path — a failed
 * proactive refresh reports the access token's own remaining validity instead of ending a session
 * whose token still authenticates every request.
 */

import {
    getCurrentTokenExpiryMs,
    ensureSessionValid,
    getDualValidAccessToken,
    setOAuth2ClientInstance,
    setExternalOauth2Token,
} from "./authTokenUtils";

// Mock Amplify auth — only the symbols authTokenUtils imports.
const mockFetchAuthSession = jest.fn();
jest.mock("aws-amplify/auth", () => ({
    fetchAuthSession: (...args: any[]) => mockFetchAuthSession(...args),
    decodeJWT: jest.fn(() => ({ payload: {} })),
}));

// A JWT whose payload base64 decodes to { sub: "u1" } — setExternalOauth2Token parses it.
const FAKE_JWT = "h." + btoa(JSON.stringify({ sub: "u1" })) + ".s";

describe("authTokenUtils session helpers", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        localStorage.clear();
        (window as any).DISABLE_COGNITO = false;
    });

    describe("getCurrentTokenExpiryMs", () => {
        it("returns Cognito idToken exp in ms", async () => {
            mockFetchAuthSession.mockResolvedValue({
                tokens: { idToken: { payload: { exp: 2000 } } },
            });
            expect(await getCurrentTokenExpiryMs()).toBe(2000 * 1000);
        });

        it("returns null when Cognito has no tokens", async () => {
            mockFetchAuthSession.mockResolvedValue({});
            expect(await getCurrentTokenExpiryMs()).toBeNull();
        });

        it("returns OAuth2 expiresAt", async () => {
            (window as any).DISABLE_COGNITO = true;
            localStorage.setItem(
                "oauth2_token",
                JSON.stringify({ accessToken: FAKE_JWT, expiresAt: 12345 })
            );
            expect(await getCurrentTokenExpiryMs()).toBe(12345);
        });
    });

    describe("ensureSessionValid (Cognito)", () => {
        it("true when a session with idToken exists", async () => {
            mockFetchAuthSession.mockResolvedValue({
                tokens: { idToken: { toString: () => "x" } },
            });
            expect(await ensureSessionValid()).toBe(true);
        });

        it("false when no tokens (refresh token dead)", async () => {
            mockFetchAuthSession.mockResolvedValue({});
            expect(await ensureSessionValid()).toBe(false);
        });

        it("false when fetchAuthSession throws", async () => {
            mockFetchAuthSession.mockRejectedValue(new Error("no session"));
            expect(await ensureSessionValid()).toBe(false);
        });

        it("passes forceRefresh through to Amplify", async () => {
            mockFetchAuthSession.mockResolvedValue({ tokens: { idToken: {} } });
            await ensureSessionValid(true);
            expect(mockFetchAuthSession).toHaveBeenCalledWith({ forceRefresh: true });
        });
    });

    describe("ensureSessionValid (OAuth2)", () => {
        beforeEach(() => {
            (window as any).DISABLE_COGNITO = true;
        });

        it("true when access token unexpired and not forcing", async () => {
            localStorage.setItem(
                "oauth2_token",
                JSON.stringify({ accessToken: FAKE_JWT, expiresAt: Date.now() + 60_000 })
            );
            expect(await ensureSessionValid()).toBe(true);
        });

        it("refreshes when access expired but refresh token present", async () => {
            const refreshToken = jest.fn().mockResolvedValue({
                accessToken: FAKE_JWT,
                refreshToken: "r2",
                expiresAt: Date.now() + 60_000,
            });
            setOAuth2ClientInstance({ refreshToken } as any);
            localStorage.setItem(
                "oauth2_token",
                JSON.stringify({ accessToken: FAKE_JWT, refreshToken: "r1", expiresAt: 1 })
            );
            expect(await ensureSessionValid()).toBe(true);
            expect(refreshToken).toHaveBeenCalled();
        });

        it("false when refresh throws", async () => {
            // Control for the proactive case below: here the access token is ALSO expired
            // (expiresAt: 1), so there is nothing left to work with and the session really is dead.
            const refreshToken = jest.fn().mockRejectedValue(new Error("expired"));
            setOAuth2ClientInstance({ refreshToken } as any);
            localStorage.setItem(
                "oauth2_token",
                JSON.stringify({ accessToken: FAKE_JWT, refreshToken: "r1", expiresAt: 1 })
            );
            expect(await ensureSessionValid()).toBe(false);
        });

        it("a failed PROACTIVE refresh keeps a session whose access token is still valid", async () => {
            // The refresh that runs while the token is still good — sessionManager's timer fires a
            // minute before expiry, and again every hour on the MAX_TIMER_MS cap for a longer-lived
            // IDP token. A transient IDP failure there used to return false, which sent
            // onExpiryTimer straight to logoutExpired(): localStorage cleared and a redirect, taking
            // any in-flight upload with it, while the access token in hand still authenticated every
            // request. The session's fate must follow the access token, not the refresh attempt.
            const refreshToken = jest.fn().mockRejectedValue(new Error("502 from the IDP"));
            setOAuth2ClientInstance({ refreshToken } as any);
            localStorage.setItem(
                "oauth2_token",
                JSON.stringify({
                    accessToken: FAKE_JWT,
                    refreshToken: "r1",
                    expiresAt: Date.now() + 4 * 60 * 60 * 1000,
                })
            );

            expect(await ensureSessionValid(true)).toBe(true);
            // The refresh was genuinely attempted — without this the assertion above is satisfied by
            // an early return that never reaches the failing branch at all.
            expect(refreshToken).toHaveBeenCalled();
            // The stored envelope is untouched, so the still-valid token remains usable.
            expect(JSON.parse(localStorage.getItem("oauth2_token")!).accessToken).toBe(FAKE_JWT);
        });

        it("false when no token at all", async () => {
            expect(await ensureSessionValid()).toBe(false);
        });
    });

    describe("OAuth2 refresh coalescing", () => {
        // An IdP that rotates refresh tokens invalidates the old one on first use, so two
        // refreshes issued with the same token mean the loser fails. The loser here is the
        // focus revalidation, whose failure sends the user to the login screen.
        const EXPIRED_ACCESS = { accessToken: FAKE_JWT, refreshToken: "r1", expiresAt: 1 };
        // The rotated token is also expired so the sequential control refreshes twice
        // instead of short-circuiting on a still-valid access token.
        const ROTATED = { accessToken: FAKE_JWT, refreshToken: "r2", expiresAt: 1 };

        beforeEach(() => {
            (window as any).DISABLE_COGNITO = true;
            localStorage.setItem("oauth2_token", JSON.stringify(EXPIRED_ACCESS));
        });

        it("shares one refresh between an API call and a session revalidation", async () => {
            let release: (token: any) => void = () => {};
            const refreshToken = jest.fn(() => {
                return new Promise((resolve) => {
                    release = resolve;
                });
            });
            setOAuth2ClientInstance({ refreshToken } as any);

            // Both entry points start before either refresh settles: apiClient's header
            // build (getDualValidAccessToken) and sessionManager's focus revalidation
            // (ensureSessionValid).
            const apiCall = getDualValidAccessToken();
            const revalidation = ensureSessionValid(false);
            release(ROTATED);

            await expect(apiCall).resolves.toBe(FAKE_JWT);
            await expect(revalidation).resolves.toBe(true);
            expect(refreshToken).toHaveBeenCalledTimes(1);
        });

        it("positive control: sequential refreshes each issue their own request", async () => {
            // Proves the single-call assertion above is not satisfied by a path that skips
            // the refresh altogether — both entry points do reach client.refreshToken.
            const refreshToken = jest.fn().mockResolvedValue(ROTATED);
            setOAuth2ClientInstance({ refreshToken } as any);

            await getDualValidAccessToken();
            await ensureSessionValid(false);

            expect(refreshToken).toHaveBeenCalledTimes(2);
        });
    });
});
