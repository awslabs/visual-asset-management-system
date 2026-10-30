/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { signOut } from "aws-amplify/auth";
import localforage from "localforage";
import { ensureSessionValid, getCurrentTokenExpiryMs } from "./authTokenUtils";

/** localStorage keys read by Auth.tsx to drive the login-screen message and route restore. */
export const SESSION_EXPIRED_KEY = "session_expired";
export const SESSION_RETURN_TO_KEY = "session_return_to";
/** localStorage key for the persisted theme preference. */
const THEME_PREFERENCE_KEY = "vams-theme-preference";
/** localStorage keys holding display preferences, kept when a sign-out clears localStorage. */
const PRESERVED_PREFERENCE_KEYS = [
    THEME_PREFERENCE_KEY,
    "ov_viewer_settings",
    "ov_camera_settings",
];

const SKEW_MS = 60_000; // refresh this long before expiry
const MIN_TIMER_MS = 5_000; // floor against tight reschedule loops
const MAX_TIMER_MS = 60 * 60 * 1000; // cap against bad exp data
const DEFAULT_TIMER_MS = 5 * 60 * 1000; // fallback when expiry is unknown
const LOCAL_STORE_CLEAR_TIMEOUT_MS = 2_000; // sign-out waits at most this long for IndexedDB
const SIGN_OUT_TIMEOUT_MS = 5_000; // sign-out waits at most this long for Amplify to revoke

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
        void logoutExpired();
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
                void logoutExpired();
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
 * Empty the browser's localforage (IndexedDB) store for a sign-out. Waits at most
 * LOCAL_STORE_CLEAR_TIMEOUT_MS, and a failure is ignored, so sign-out always proceeds.
 */
export async function clearLocalStore(): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
        await Promise.race([
            localforage.clear(),
            new Promise<void>((resolve) => {
                timer = setTimeout(resolve, LOCAL_STORE_CLEAR_TIMEOUT_MS);
            }),
        ]);
    } catch {
        // Sign-out continues without the store cleared.
    } finally {
        clearTimeout(timer);
    }
}

/**
 * Sign out of Amplify and wait for it, so the Cognito refresh token is revoked. Amplify reads the
 * stored tokens before it calls RevokeToken, so every sign-out runs this BEFORE it clears
 * localStorage; a sign-out that clears storage first leaves Amplify nothing to revoke. Waits at
 * most SIGN_OUT_TIMEOUT_MS, and a failure is ignored, so a sign-out always proceeds.
 */
export async function revokeSession(): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
        await Promise.race([
            signOut(),
            new Promise<void>((resolve) => {
                timer = setTimeout(resolve, SIGN_OUT_TIMEOUT_MS);
            }),
        ]);
    } catch (error) {
        // Sign-out continues; the local session is still cleared.
        console.log("User sign out error", error);
    } finally {
        clearTimeout(timer);
    }
}

/**
 * Clear localStorage for a sign-out while keeping the display preferences (theme and the
 * Online 3D Viewer settings). Every other key, including Amplify's token keys, is removed.
 * Run it after revokeSession().
 */
export function clearStoredSession(): void {
    const kept = PRESERVED_PREFERENCE_KEYS.map((key) => [key, localStorage.getItem(key)] as const);
    localStorage.clear();
    for (const [key, value] of kept) {
        if (value !== null) {
            localStorage.setItem(key, value);
        }
    }
}

/**
 * Tear down a session that can no longer be refreshed: stop the timer, sign out of Amplify (which
 * revokes the refresh token when it still can), clear localStorage (keeping display preferences),
 * record the expired flag + the route to return to, empty the localforage store, then redirect to
 * the login screen. Idempotent — first caller wins.
 */
export async function logoutExpired(returnTo: string = window.location.hash): Promise<void> {
    if (loggingOut) {
        return;
    }
    loggingOut = true;
    clearExpiryTimer();

    await revokeSession();
    clearStoredSession();
    localStorage.setItem(SESSION_EXPIRED_KEY, "true");
    if (returnTo) {
        localStorage.setItem(SESSION_RETURN_TO_KEY, returnTo);
    }
    await clearLocalStore();
    navigation.redirectToLogin();
}
