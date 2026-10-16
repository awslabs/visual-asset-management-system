/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Recovery for code-split chunks that fail to download.
 *
 * `React.lazy` caches the promise its factory returns, so a rejected `import()` — a dropped connection,
 * or a chunk that a newer deployment pruned from the web bucket — is re-thrown on every later render and
 * an error boundary that only clears its own state can never recover it. Browsers may also keep the
 * failed module in their module map, so a second `import()` of the same URL can fail without a new
 * request. `lazyWithRetry` therefore retries once and then falls back to one full page reload, guarded in
 * `sessionStorage` so a persistent failure ends on an error screen instead of a reload loop.
 *
 * Vite's `vite:preloadError` event needs no listener of its own: when it is not cancelled, the same
 * error rejects the `import()` promise and reaches these handlers. A global listener that reloaded the
 * page would also fire for every lazily loaded feature after start-up (viewer plugins, editors) and
 * discard whatever the user had on screen.
 */

import React, { ComponentType, LazyExoticComponent } from "react";

/** How long the application shell may load before the loading screen offers a reload. */
export const APP_LOAD_SLOW_NOTICE_MS = 10_000;

/** `sessionStorage` key holding the time of the last automatic reload after a chunk failure. */
export const CHUNK_RELOAD_GUARD_KEY = "vams:chunk-reload-at";

/** An automatic reload is skipped when the previous one happened less than this long ago. */
export const CHUNK_RELOAD_GUARD_WINDOW_MS = 60_000;

/** Wait before the single in-page retry of a failed chunk import. */
export const CHUNK_RETRY_DELAY_MS = 1_500;

// Messages browsers and Vite use for a module or preload that could not be fetched or evaluated as a
// script. Matched case-insensitively against `Error.message`.
const CHUNK_ERROR_PATTERNS: RegExp[] = [
    /failed to fetch dynamically imported module/i, // Chromium
    /error loading dynamically imported module/i, // Firefox
    /importing a module script failed/i, // Safari
    /failed to load module script/i, // MIME type refused (an HTML error page served for a chunk)
    /unable to preload css/i, // Vite CSS preload
    /loading (css )?chunk [\w-]+ failed/i, // webpack-style wording used by some bundled libraries
];

/** True when `error` reports a code-split chunk that could not be downloaded or loaded. */
export function isChunkLoadError(error: unknown): boolean {
    if (!error) {
        return false;
    }
    const name = (error as { name?: unknown }).name;
    if (name === "ChunkLoadError") {
        return true;
    }
    const message =
        typeof error === "string"
            ? error
            : typeof (error as { message?: unknown }).message === "string"
            ? ((error as { message: string }).message as string)
            : "";
    return CHUNK_ERROR_PATTERNS.some((pattern) => pattern.test(message));
}

interface ReloadDependencies {
    storage?: Pick<Storage, "getItem" | "setItem" | "removeItem"> | null;
    reload?: () => void;
    now?: () => number;
}

function defaultStorage(): ReloadDependencies["storage"] {
    try {
        return window.sessionStorage;
    } catch {
        // Storage can throw when it is disabled by browser policy.
        return null;
    }
}

/**
 * Reloads the page once for a chunk failure. Returns false, and does nothing, when a reload already
 * happened inside `CHUNK_RELOAD_GUARD_WINDOW_MS` or when the guard cannot be recorded — without the guard
 * a chunk that never downloads would reload the page indefinitely.
 */
export function reloadOnceForChunkError(deps: ReloadDependencies = {}): boolean {
    const storage = deps.storage === undefined ? defaultStorage() : deps.storage;
    const reload = deps.reload ?? (() => window.location.reload());
    const now = (deps.now ?? Date.now)();
    if (!storage) {
        return false;
    }
    try {
        const last = Number(storage.getItem(CHUNK_RELOAD_GUARD_KEY));
        if (Number.isFinite(last) && last > 0 && now - last < CHUNK_RELOAD_GUARD_WINDOW_MS) {
            return false;
        }
        storage.setItem(CHUNK_RELOAD_GUARD_KEY, String(now));
    } catch {
        return false;
    }
    reload();
    return true;
}

/** Reloads the page on a user's request, clearing the automatic-reload guard first. */
export function reloadPage(deps: ReloadDependencies = {}): void {
    const storage = deps.storage === undefined ? defaultStorage() : deps.storage;
    try {
        storage?.removeItem(CHUNK_RELOAD_GUARD_KEY);
    } catch {
        // A storage failure must not block the reload itself.
    }
    (deps.reload ?? (() => window.location.reload()))();
}

interface RetryOptions extends ReloadDependencies {
    retries?: number;
    retryDelayMs?: number;
}

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Runs a dynamic-import factory, retrying chunk failures. When the retries are spent it reloads the page
 * once (see `reloadOnceForChunkError`) and returns a promise that never settles, so the Suspense fallback
 * stays on screen until the browser navigates. Other errors, and chunk failures once the reload guard has
 * tripped, are re-thrown for the nearest error boundary.
 */
export async function importWithRetry<T>(
    factory: () => Promise<T>,
    options: RetryOptions = {}
): Promise<T> {
    const retries = options.retries ?? 1;
    const retryDelayMs = options.retryDelayMs ?? CHUNK_RETRY_DELAY_MS;
    let attempt = 0;
    for (;;) {
        try {
            return await factory();
        } catch (error) {
            if (!isChunkLoadError(error)) {
                throw error;
            }
            if (attempt < retries) {
                attempt += 1;
                console.warn(`Chunk failed to load, retrying (${attempt}/${retries})`, error);
                await wait(retryDelayMs);
                continue;
            }
            if (reloadOnceForChunkError(options)) {
                console.warn("Chunk failed to load after retrying, reloading the page", error);
                return new Promise<T>(() => {});
            }
            throw error;
        }
    }
}

/** `React.lazy` with `importWithRetry` applied to the factory. */
export function lazyWithRetry<T extends ComponentType<any>>(
    factory: () => Promise<{ default: T }>,
    options?: RetryOptions
): LazyExoticComponent<T> {
    return React.lazy(() => importWithRetry(factory, options));
}
