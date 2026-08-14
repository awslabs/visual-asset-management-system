/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** How long to wait for a bundle before reporting a failure instead of waiting forever. */
export const SCRIPT_LOAD_TIMEOUT_MS = 60000;

/** Marks a tag whose load event has already fired, so a later caller need not wait for it. */
const LOADED_ATTRIBUTE = "data-vams-script-loaded";

/**
 * Loads still running, keyed by src. This is what makes concurrent callers share one load rather
 * than each injecting its own tag or tearing out somebody else's.
 */
const inFlightLoads = new Map<string, Promise<void>>();

/**
 * Injects a `<script src>` once and resolves when it has executed.
 *
 * Viewer plugins self-host their library bundles and load them as script tags on demand. Several
 * viewers can mount at once, and one viewer can remount while its first load is still downloading,
 * so this has to be safe to call concurrently and repeatedly for the same src:
 *
 * - Overlapping callers share a single in-flight load. Loaders that instead resolved as soon as a tag
 *   existed handed back a "loaded" bundle whose global was still undefined; loaders that removed the
 *   existing tag to force a reload left the first caller's promise unsettled forever, because
 *   removing an in-flight script does not reliably fire `onerror`.
 * - A tag left behind by an attempt that finished without executing is discarded and re-injected, but
 *   only when nothing is in flight for that src.
 * - A load that never settles fails on a timeout rather than hanging, so the caller can surface an
 *   error and a later attempt can retry.
 */
export function loadExternalScript(
    src: string,
    options: { asModule?: boolean; timeoutMs?: number; isReady?: () => boolean } = {}
): Promise<void> {
    const { asModule = false, timeoutMs = SCRIPT_LOAD_TIMEOUT_MS, isReady } = options;

    // Checked before anything else so that overlapping callers always join the running load. Doing
    // this first is what preserves deduplication even when isReady() says a reload is needed.
    const running = inFlightLoads.get(src);
    if (running) {
        return running;
    }

    const existing = document.querySelector(`script[src="${src}"]`) as HTMLScriptElement | null;

    // A tag that has already executed only short-circuits when the library it defines is still
    // usable. Viewers that publish a window global tear that global down when they unmount, leaving a
    // tag that ran but a library that is gone; short-circuiting there would resolve without
    // re-executing and the caller would fail on a missing global.
    if (existing?.hasAttribute(LOADED_ATTRIBUTE) && (!isReady || isReady())) {
        return Promise.resolve();
    }

    const load = new Promise<void>((resolve, reject) => {
        // Nothing is in flight for this src (guaranteed above), so any tag here either never executed
        // or defines a library that is no longer usable. Re-injecting is the only way forward.
        existing?.remove();

        const script = document.createElement("script");
        script.src = src;
        if (asModule) {
            script.type = "module";
        } else {
            script.async = true;
        }

        let settled = false;
        const finish = (complete: () => void) => {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            complete();
        };

        const timer = setTimeout(() => {
            finish(() => {
                script.remove();
                reject(new Error(`Timed out after ${timeoutMs / 1000}s loading script: ${src}`));
            });
        }, timeoutMs);

        script.onload = () =>
            finish(() => {
                script.setAttribute(LOADED_ATTRIBUTE, "true");
                resolve();
            });

        script.onerror = () => finish(() => reject(new Error(`Failed to load script: ${src}`)));

        document.head.appendChild(script);
    });

    inFlightLoads.set(src, load);

    // Released once the load settles, so the promise is only ever shared by callers that overlap in
    // time. Keeping a settled promise would break reloading: viewers tear their globals down when
    // they unmount, and a retained promise would resolve instantly without re-injecting, leaving the
    // caller to fail on a missing global.
    return load.finally(() => {
        if (inFlightLoads.get(src) === load) {
            inFlightLoads.delete(src);
        }
    });
}

/**
 * Forgets that a src was loaded, so the next call re-injects it. For a cleanup path that removes the
 * tag and drops the library's global.
 */
export function resetExternalScript(src: string): void {
    inFlightLoads.delete(src);
    document
        .querySelectorAll(`script[src="${src}"]`)
        .forEach((script) => script.removeAttribute(LOADED_ATTRIBUTE));
}
