/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The SDK bundle that `web/customInstalls/miris/mirisInstall.js` builds and publishes under
 * `public/viewers/miris/` when this viewer is enabled. It is an ES module whose chunks and `.wasm`
 * files sit beside it, and it is imported at runtime rather than bundled into the web app.
 */
export const MIRIS_SDK_URL = "/viewers/miris/miris-sdk.js";

/** The exports of the bundle's entry, `web/customInstalls/miris/mirisSdkEntry.js`. */
export interface MirisSdkModule {
    /** Evaluates `@miris-inc/components`, which registers the custom elements. */
    defineElements: () => Promise<unknown>;
}

type WebGpuNavigator = Navigator & { gpu?: { requestAdapter: () => Promise<unknown> } };

export class MirisStreamDependencyManager {
    private static loaded = false;
    private static pending: Promise<void> | null = null;

    /**
     * Whether the SDK can render here. It renders only with WebGPU and decides by requesting an
     * adapter (`navigator.gpu.requestAdapter()`) when its bundle evaluates, so the same request
     * answers without loading the SDK.
     */
    static async isDeviceSupported(): Promise<boolean> {
        try {
            return (await (navigator as WebGpuNavigator).gpu?.requestAdapter()) != null;
        } catch {
            return false;
        }
    }

    /**
     * Registers the Miris custom elements (<miris-scene>, <miris-camera>, <miris-stream>,
     * <miris-group>, <miris-grid>).
     */
    static async loadMirisStream(): Promise<void> {
        if (MirisStreamDependencyManager.loaded) return;
        const attempt = (MirisStreamDependencyManager.pending ??= (async () => {
            const sdk: MirisSdkModule = await import(/* @vite-ignore */ MIRIS_SDK_URL);
            await sdk.defineElements();
            MirisStreamDependencyManager.loaded = true;
        })().finally(() => {
            if (MirisStreamDependencyManager.pending === attempt) {
                MirisStreamDependencyManager.pending = null;
            }
        }));
        await attempt;
    }

    /**
     * Drops the in-flight load so the next `loadMirisStream()` starts a new attempt instead of
     * joining one that never settled. The abandoned attempt still marks the SDK loaded if it
     * eventually completes.
     */
    static abandonPendingLoad(): void {
        MirisStreamDependencyManager.pending = null;
    }

    /**
     * Lets the next open run the load again, which the browser's module cache answers at once.
     * Custom elements cannot be unregistered, and the SDK settles its renderer once per page, so
     * nothing is chosen again.
     */
    static cleanup(): void {
        MirisStreamDependencyManager.loaded = false;
    }
}
