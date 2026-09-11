/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    ensureValidSession,
    computeTimerDelayMs,
    scheduleExpiryTimer,
    clearExpiryTimer,
    logoutExpired,
    navigation,
    SESSION_EXPIRED_KEY,
    SESSION_RETURN_TO_KEY,
    __resetForTests,
} from "./sessionManager";
import * as tokenUtils from "./authTokenUtils";

jest.mock("./authTokenUtils");
const mockSignOut = jest.fn().mockResolvedValue(undefined);
jest.mock("aws-amplify/auth", () => ({
    signOut: (...a: any[]) => mockSignOut(...a),
}));

const utils = tokenUtils as jest.Mocked<typeof tokenUtils>;

describe("sessionManager", () => {
    let redirectSpy: jest.SpyInstance;

    beforeEach(() => {
        jest.clearAllMocks();
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
        it("is idempotent — only the first call redirects", () => {
            logoutExpired("#/assets");
            logoutExpired("#/other");
            expect(localStorage.getItem(SESSION_RETURN_TO_KEY)).toBe("#/assets");
            // Second call did not redirect again.
            expect(redirectSpy).toHaveBeenCalledTimes(1);
        });

        it("preserves theme across the localStorage wipe", () => {
            localStorage.setItem("vams-theme-preference", "light");
            localStorage.setItem("user", JSON.stringify({ username: "u" }));
            logoutExpired("#/assets");
            expect(localStorage.getItem("vams-theme-preference")).toBe("light");
            expect(localStorage.getItem("user")).toBeNull();
            expect(localStorage.getItem(SESSION_EXPIRED_KEY)).toBe("true");
        });
    });
});
