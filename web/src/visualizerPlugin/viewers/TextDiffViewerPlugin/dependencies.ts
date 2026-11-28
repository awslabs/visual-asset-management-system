/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * TextDiff Dependency Manager
 *
 * Dynamically loads `react-diff-viewer-continued` so the diff library is code-split into its own
 * chunk and kept out of the base bundle. Unlike the 3D viewers' dependency managers (which inject a
 * self-hosted `<script>` bundle), this one uses a native dynamic `import()` of the npm package —
 * Vite emits a separate chunk that is only fetched the first time the Text Diff viewer is selected.
 *
 * Concurrent callers share ONE in-flight import; the resolved module is cached for the life of the
 * page. The loaded component is retrieved with {@link getDiffViewer}.
 */
export class TextDiffDependencyManager {
    private static loadPromise: Promise<any> | null = null;
    private static module: any = null;

    /**
     * Load the diff-viewer module. Safe to call concurrently and repeatedly — callers share a
     * single import and the cached module is reused thereafter.
     */
    static async loadDiffViewer(): Promise<void> {
        if (this.module) {
            return;
        }
        if (this.loadPromise) {
            await this.loadPromise;
            return;
        }

        // Dynamic import → Vite splits react-diff-viewer-continued into its own lazy chunk.
        this.loadPromise = import("react-diff-viewer-continued");
        try {
            this.module = await this.loadPromise;
        } finally {
            // Cleared once settled so a failed load can be retried; concurrent callers already hold
            // this exact promise, so clearing it does not reintroduce a load race.
            this.loadPromise = null;
        }
    }

    /** True when the diff library has been loaded and is usable. */
    static isLoaded(): boolean {
        return !!this.module;
    }

    /** The default-exported diff-viewer React component. Throws if not yet loaded. */
    static getDiffViewer(): any {
        if (!this.module) {
            throw new Error("react-diff-viewer-continued not loaded");
        }
        return this.module.default;
    }

    /** The DiffMethod enum from the library (WORDS, LINES, etc.). Throws if not yet loaded. */
    static getDiffMethod(): any {
        if (!this.module) {
            throw new Error("react-diff-viewer-continued not loaded");
        }
        return this.module.DiffMethod;
    }

    /**
     * Cleanup (no-op). The imported module stays resident for reuse across viewer opens, matching
     * the other dependency managers that keep their library loaded.
     */
    static cleanup(): void {
        console.log("TextDiff viewer cleanup called (library remains loaded)");
    }
}
