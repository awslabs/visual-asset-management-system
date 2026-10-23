/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    ensureValidSession,
    computeTimerDelayMs,
    scheduleExpiryTimer,
    clearExpiryTimer,
    clearLocalStore,
    clearStoredSession,
    logoutExpired,
    navigation,
    revokeSession,
    SESSION_EXPIRED_KEY,
    SESSION_RETURN_TO_KEY,
    __resetForTests,
} from "./sessionManager";
import * as tokenUtils from "./authTokenUtils";

/** A key in the shape Amplify v6 stores the Cognito refresh token under. */
const AMPLIFY_REFRESH_TOKEN_KEY = "CognitoIdentityServiceProvider.client-1.user-1.refreshToken";

jest.mock("./authTokenUtils");
const mockSignOut = jest.fn().mockResolvedValue(undefined);
jest.mock("aws-amplify/auth", () => ({
    signOut: (...a: any[]) => mockSignOut(...a),
}));
const mockLocalforageClear = jest.fn();
jest.mock("localforage", () => ({
    clear: (...a: any[]) => mockLocalforageClear(...a),
}));

const utils = tokenUtils as jest.Mocked<typeof tokenUtils>;

describe("sessionManager", () => {
    let redirectSpy: jest.SpyInstance;

    beforeEach(() => {
        jest.clearAllMocks();
        mockSignOut.mockReset().mockResolvedValue(undefined);
        mockLocalforageClear.mockReset().mockResolvedValue(undefined);
        jest.useRealTimers();
        localStorage.clear();
        __resetForTests();
        // window.location is non-configurable in jsdom and cannot be stubbed; spy on the
        // navigation seam instead to observe the login redirect without performing it.
        redirectSpy = jest.spyOn(navigation, "redirectToLogin").mockImplementation(() => {});
    });

    afterEach(() => {
        redirectSpy.mockRestore();
    });

    describe("computeTimerDelayMs (pure)", () => {
        it("schedules ~60s before expiry", () => {
            const now = 1_000_000;
            expect(computeTimerDelayMs(now + 300_000, now)).toBe(300_000 - 60_000);
        });
        it("clamps to MIN when already near/after expiry", () => {
            const now = 1_000_000;
            expect(computeTimerDelayMs(now + 1_000, now)).toBe(5_000);
        });
        it("falls back to default when expiry is null", () => {
            expect(computeTimerDelayMs(null, 0)).toBe(5 * 60 * 1000);
        });
        it("caps absurdly large delays", () => {
            expect(computeTimerDelayMs(10 ** 15, 0)).toBe(60 * 60 * 1000);
        });
    });

    describe("ensureValidSession single-flight", () => {
        it("coalesces concurrent calls into one underlying validation", async () => {
            let resolve!: (v: boolean) => void;
            utils.ensureSessionValid.mockReturnValue(new Promise<boolean>((r) => (resolve = r)));
            const p1 = ensureValidSession();
            const p2 = ensureValidSession();
            resolve(true);
            await Promise.all([p1, p2]);
            expect(utils.ensureSessionValid).toHaveBeenCalledTimes(1);
        });

        it("allows a fresh call after the in-flight one settles", async () => {
            utils.ensureSessionValid.mockResolvedValue(true);
            await ensureValidSession();
            await ensureValidSession();
            expect(utils.ensureSessionValid).toHaveBeenCalledTimes(2);
        });
    });

    describe("scheduleExpiryTimer", () => {
        it("refreshes and reschedules when the timer fires successfully", async () => {
            jest.useFakeTimers();
            utils.getCurrentTokenExpiryMs.mockResolvedValue(Date.now() + 120_000);
            utils.ensureSessionValid.mockResolvedValue(true);

            await scheduleExpiryTimer();
            // Fire the pending timer.
            await jest.advanceTimersByTimeAsync(120_000 - 60_000 + 1);

            expect(utils.ensureSessionValid).toHaveBeenCalledWith(true);
            // Did not log out.
            expect(localStorage.getItem(SESSION_EXPIRED_KEY)).toBeNull();
        });

        it("logs out when the scheduled refresh fails", async () => {
            jest.useFakeTimers();
            utils.getCurrentTokenExpiryMs.mockResolvedValue(Date.now() + 120_000);
            utils.ensureSessionValid.mockResolvedValue(false);

            await scheduleExpiryTimer();
            await jest.advanceTimersByTimeAsync(120_000 - 60_000 + 1);

            expect(localStorage.getItem(SESSION_EXPIRED_KEY)).toBe("true");
            expect(redirectSpy).toHaveBeenCalledTimes(1);
        });
    });

    describe("logoutExpired", () => {
        it("is idempotent — only the first call redirects", async () => {
            const first = logoutExpired("#/assets");
            const second = logoutExpired("#/other");
            await Promise.all([first, second]);
            expect(localStorage.getItem(SESSION_RETURN_TO_KEY)).toBe("#/assets");
            // Second call did not redirect again.
            expect(redirectSpy).toHaveBeenCalledTimes(1);
        });

        it("preserves theme across the localStorage wipe", async () => {
            localStorage.setItem("vams-theme-preference", "light");
            localStorage.setItem("user", JSON.stringify({ username: "u" }));
            await logoutExpired("#/assets");
            expect(localStorage.getItem("vams-theme-preference")).toBe("light");
            expect(localStorage.getItem("user")).toBeNull();
            expect(localStorage.getItem(SESSION_EXPIRED_KEY)).toBe("true");
        });

        it("preserves the viewer settings across the localStorage wipe", async () => {
            localStorage.setItem("ov_viewer_settings", '{"a":1}');
            localStorage.setItem("ov_camera_settings", '{"b":2}');
            await logoutExpired("#/assets");
            expect(localStorage.getItem("ov_viewer_settings")).toBe('{"a":1}');
            expect(localStorage.getItem("ov_camera_settings")).toBe('{"b":2}');
        });

        it("signs out of Amplify before clearing localStorage or the localforage store", async () => {
            const order: string[] = [];
            const storageClear = jest.spyOn(Storage.prototype, "clear").mockImplementation(() => {
                order.push("localStorage.clear");
            });
            mockLocalforageClear.mockImplementation(async () => {
                order.push("localforage.clear");
            });
            mockSignOut.mockImplementation(async () => {
                order.push("signOut:start");
                await Promise.resolve();
                order.push("signOut:done");
            });
            redirectSpy.mockImplementation(() => {
                order.push("redirect");
            });

            try {
                await logoutExpired("#/assets");
            } finally {
                storageClear.mockRestore();
            }

            expect(order).toEqual([
                "signOut:start",
                "signOut:done",
                "localStorage.clear",
                "localforage.clear",
                "redirect",
            ]);
        });

        it("leaves the stored tokens in place while Amplify reads them to revoke the session", async () => {
            localStorage.setItem(AMPLIFY_REFRESH_TOKEN_KEY, "refresh-1");
            let seenByAmplify: string | null = "not read";
            mockSignOut.mockImplementation(async () => {
                // Amplify loads the tokens asynchronously before it calls RevokeToken.
                await Promise.resolve();
                seenByAmplify = localStorage.getItem(AMPLIFY_REFRESH_TOKEN_KEY);
            });

            await logoutExpired("#/assets");

            expect(seenByAmplify).toBe("refresh-1");
            expect(localStorage.getItem(AMPLIFY_REFRESH_TOKEN_KEY)).toBeNull();
        });

        it("waits at most five seconds for Amplify, then clears storage and redirects", async () => {
            jest.useFakeTimers();
            mockSignOut.mockReturnValue(new Promise(() => undefined));
            localStorage.setItem("user", JSON.stringify({ username: "u" }));

            const pending = logoutExpired("#/assets");

            await jest.advanceTimersByTimeAsync(4_999);
            expect(localStorage.getItem("user")).not.toBeNull();
            expect(redirectSpy).not.toHaveBeenCalled();

            await jest.advanceTimersByTimeAsync(1);
            await pending;
            expect(localStorage.getItem("user")).toBeNull();
            expect(localStorage.getItem(SESSION_EXPIRED_KEY)).toBe("true");
            expect(redirectSpy).toHaveBeenCalledTimes(1);
        });

        it("still clears storage and redirects when Amplify sign-out fails", async () => {
            mockSignOut.mockRejectedValue(new Error("network down"));
            localStorage.setItem("user", JSON.stringify({ username: "u" }));

            await logoutExpired("#/assets");

            expect(localStorage.getItem("user")).toBeNull();
            expect(localStorage.getItem(SESSION_EXPIRED_KEY)).toBe("true");
            expect(localStorage.getItem(SESSION_RETURN_TO_KEY)).toBe("#/assets");
            expect(mockLocalforageClear).toHaveBeenCalledTimes(1);
            expect(redirectSpy).toHaveBeenCalledTimes(1);
        });
    });

    describe("revokeSession", () => {
        it("signs out of Amplify once", async () => {
            await revokeSession();
            expect(mockSignOut).toHaveBeenCalledTimes(1);
        });

        it("resolves when Amplify sign-out fails", async () => {
            mockSignOut.mockRejectedValue(new Error("network down"));
            await expect(revokeSession()).resolves.toBeUndefined();
        });

        it("stops waiting after five seconds when Amplify sign-out does not finish", async () => {
            jest.useFakeTimers();
            mockSignOut.mockReturnValue(new Promise(() => undefined));
            let settled = false;
            const pending = revokeSession().then(() => {
                settled = true;
            });

            await jest.advanceTimersByTimeAsync(4_999);
            expect(settled).toBe(false);
            await jest.advanceTimersByTimeAsync(1);
            await pending;
            expect(settled).toBe(true);
        });
    });

    describe("clearLocalStore", () => {
        it("clears the localforage store", async () => {
            await clearLocalStore();
            expect(mockLocalforageClear).toHaveBeenCalledTimes(1);
        });

        it("resolves when clearing fails", async () => {
            mockLocalforageClear.mockRejectedValue(new Error("IndexedDB unavailable"));
            await expect(clearLocalStore()).resolves.toBeUndefined();
        });

        it("stops waiting after two seconds when clearing does not finish", async () => {
            jest.useFakeTimers();
            mockLocalforageClear.mockReturnValue(new Promise(() => {}));
            let settled = false;
            const pending = clearLocalStore().then(() => {
                settled = true;
            });

            await jest.advanceTimersByTimeAsync(1_999);
            expect(settled).toBe(false);
            await jest.advanceTimersByTimeAsync(1);
            await pending;
            expect(settled).toBe(true);
        });
    });

    describe("clearStoredSession", () => {
        it("keeps the display preferences and removes every other key", () => {
            localStorage.setItem("vams-theme-preference", "light");
            localStorage.setItem("ov_viewer_settings", '{"a":1}');
            localStorage.setItem("ov_camera_settings", '{"b":2}');
            localStorage.setItem("user", JSON.stringify({ username: "u" }));
            localStorage.setItem("oauth2_token", "t");
            localStorage.setItem("CognitoIdentityServiceProvider.client.u.idToken", "x");

            clearStoredSession();

            expect(localStorage.getItem("vams-theme-preference")).toBe("light");
            expect(localStorage.getItem("ov_viewer_settings")).toBe('{"a":1}');
            expect(localStorage.getItem("ov_camera_settings")).toBe('{"b":2}');
            expect(localStorage.length).toBe(3);
        });

        it("does not create preference keys that were not set", () => {
            localStorage.setItem("user", "u");

            clearStoredSession();

            expect(localStorage.length).toBe(0);
        });
    });
});
