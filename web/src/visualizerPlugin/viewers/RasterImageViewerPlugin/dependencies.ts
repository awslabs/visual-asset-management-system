/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Raster image decoders for the TIFF/TGA viewer.
 *
 * `utif2` (TIFF), its zlib port `pako` and `tga-js` (TGA) are pure JavaScript, so they run under the
 * base Content Security Policy with no `unsafe-eval`. They are loaded with a native dynamic
 * `import()`: Vite emits them as their own chunks, fetched the first time the viewer opens and kept
 * out of the base bundle.
 *
 * Concurrent callers share ONE in-flight import and the resolved modules are cached for the life of
 * the page.
 */

import type * as UtifTypes from "utif2";
import type TgaClass from "tga-js";

export type UtifModule = typeof UtifTypes;
export type TgaConstructor = typeof TgaClass;

/** The part of `pako` the viewer uses to bound a deflate strip's output. */
export interface PakoModule {
    Inflate: new (options: { raw?: boolean; chunkSize?: number }) => {
        /** Called with each block of output; the viewer replaces it to bound the output. */
        onData: (chunk: Uint8Array) => void;
        push(data: Uint8Array, last?: boolean): boolean;
    };
}

export class RasterImageDependencyManager {
    private static loadPromise: Promise<void> | null = null;
    private static utif: UtifModule | null = null;
    private static tga: TgaConstructor | null = null;
    private static pako: PakoModule | null = null;

    /** Load the decoders. Safe to call concurrently and repeatedly. */
    static async loadDecoders(): Promise<void> {
        if (this.utif && this.tga && this.pako) {
            return;
        }
        if (!this.loadPromise) {
            this.loadPromise = (async () => {
                const [utifModule, tgaModule, pakoModule] = await Promise.all([
                    import("utif2"),
                    import("tga-js"),
                    import("pako"),
                ]);
                // utif2 and pako are CommonJS: depending on the bundler the API is the namespace
                // itself or its `default`.
                this.utif = ((utifModule as any).default ?? utifModule) as UtifModule;
                this.tga = ((tgaModule as any).default ?? tgaModule) as TgaConstructor;
                this.pako = ((pakoModule as any).default ?? pakoModule) as PakoModule;
            })();
        }
        try {
            await this.loadPromise;
        } finally {
            // Cleared once settled so a failed load can be retried; concurrent callers already hold
            // this exact promise.
            this.loadPromise = null;
        }
    }

    static isLoaded(): boolean {
        return !!this.utif && !!this.tga && !!this.pako;
    }

    /** The TIFF decoder. Throws if {@link loadDecoders} has not completed. */
    static getUtif(): UtifModule {
        if (!this.utif) throw new Error("utif2 not loaded");
        return this.utif;
    }

    /** The TGA decoder class. Throws if {@link loadDecoders} has not completed. */
    static getTga(): TgaConstructor {
        if (!this.tga) throw new Error("tga-js not loaded");
        return this.tga;
    }

    /** The zlib port. Throws if {@link loadDecoders} has not completed. */
    static getPako(): PakoModule {
        if (!this.pako) throw new Error("pako not loaded");
        return this.pako;
    }

    /** Cleanup (no-op). The modules stay resident for reuse across viewer opens. */
    static cleanup(): void {
        console.log("TIFF/TGA viewer cleanup called (decoders remain loaded)");
    }
}
