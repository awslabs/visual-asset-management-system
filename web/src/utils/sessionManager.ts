/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { signOut } from "aws-amplify/auth";
import { ensureSessionValid, getCurrentTokenExpiryMs } from "./authTokenUtils";

/** localStorage keys read by Auth.tsx to drive the login-screen message and route restore. */
export const SESSION_EXPIRED_KEY = "session_expired";
export const SESSION_RETURN_TO_KEY = "session_return_to";
/** localStorage key for the persisted theme preference (preserved across forced logout). */
const THEME_PREFERENCE_KEY = "vams-theme-preference";

const SKEW_MS = 60_000; // refresh this long before expiry
const MIN_TIMER_MS = 5_000; // floor against tight reschedule loops
const MAX_TIMER_MS = 60 * 60 * 1000; // cap against bad exp data
const DEFAULT_TIMER_MS = 5 * 60 * 1000; // fallback when expiry is unknown

/** Navigation seam — isolates the untestable browser redirect so it can be spied in tests. */
export const navigation = {
    redirectToLogin(): void {
        window.location.href = "/";
    },
};

let inFlight: Promise<boolean> | null = null;
let expiryTimer: ReturnType<typeof setTimeout> | null = null;
let loggingOut = false;

/**
 * Validate/refresh the session, coalescing concurrent callers onto one underlying
 * call so parallel API requests don't trigger N refreshes / N redirects.
 */
export function ensureValidSession(forceRefresh = false): Promise<boolean> {
    if (inFlight) {
        return inFlight;
    }
    inFlight = ensureSessionValid(forceRefresh).finally(() => {
        inFlight = null;
    });
    return inFlight;
}

export function getAccessTokenExpiry(): Promise<number | null> {
    return getCurrentTokenExpiryMs();
}

/** Pure: clamped delay until the next proactive refresh. */
export function computeTimerDelayMs(expiryMs: number | null, nowMs: number): number {
    const delay = expiryMs === null ? DEFAULT_TIMER_MS : expiryMs - nowMs - SKEW_MS;
    return Math.min(Math.max(delay, MIN_TIMER_MS), MAX_TIMER_MS);
}

export function clearExpiryTimer(): void {
    if (expiryTimer) {
        clearTimeout(expiryTimer);
        expiryTimer = null;
    }
}

/** @internal Test-only: reset module state between tests. */
export function __resetForTests(): void {
    inFlight = null;
    clearExpiryTimer();
    loggingOut = false;
}

/** (Re)arm a single timer aligned to the real token expiry. */
export async function scheduleExpiryTimer(): Promise<void> {
    clearExpiryTimer();
    const expiry = await getAccessTokenExpiry();
    const delay = computeTimerDelayMs(expiry, Date.now());
    expiryTimer = setTimeout(() => {
        void onExpiryTimer();
    }, delay);
}

async function onExpiryTimer(): Promise<void> {
    const ok = await ensureValidSession(true);
    if (ok) {
        await scheduleExpiryTimer();
    } else {
        logoutExpired();
    }
}

/**
 * Revalidate when the tab regains focus/visibility — covers timer throttling during
 * laptop sleep or backgrounded tabs. Logs out if the session is found dead.
 */
export function registerFocusRevalidation(): () => void {
    const handler = () => {
        if (document.visibilityState === "hidden") {
            return;
        }
        void ensureValidSession().then((ok) => {
            if (!ok) {
                logoutExpired();
            }
        });
    };
    window.addEventListener("focus", handler);
    document.addEventListener("visibilitychange", handler);
    return () => {
        window.removeEventListener("focus", handler);
        document.removeEventListener("visibilitychange", handler);
    };
}

/**
 * Tear down a session that can no longer be refreshed: stop the timer, sign out of
 * Amplify, wipe localStorage (preserving theme), record the expired flag + the route
 * to return to, then redirect to the login screen. Idempotent — first caller wins.
 */
export function logoutExpired(returnTo: string = window.location.hash): void {
    if (loggingOut) {
        return;
    }
    loggingOut = true;
    clearExpiryTimer();

    const theme = localStorage.getItem(THEME_PREFERENCE_KEY);
    void signOut().catch(() => {});
    localStorage.clear();
    if (theme) {
        localStorage.setItem(THEME_PREFERENCE_KEY, theme);
    }
    localStorage.setItem(SESSION_EXPIRED_KEY, "true");
    if (returnTo) {
        localStorage.setItem(SESSION_RETURN_TO_KEY, returnTo);
    }
    navigation.redirectToLogin();
}
