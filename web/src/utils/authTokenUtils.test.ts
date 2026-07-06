/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    getCurrentTokenExpiryMs,
    ensureSessionValid,
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
            const refreshToken = jest.fn().mockRejectedValue(new Error("expired"));
            setOAuth2ClientInstance({ refreshToken } as any);
            localStorage.setItem(
                "oauth2_token",
                JSON.stringify({ accessToken: FAKE_JWT, refreshToken: "r1", expiresAt: 1 })
            );
            expect(await ensureSessionValid()).toBe(false);
        });

        it("false when no token at all", async () => {
            expect(await ensureSessionValid()).toBe(false);
        });
    });
});
