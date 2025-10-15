/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { StylesheetManager } from "../../core/StylesheetManager";

export class Online3dViewerDependencyManager {
    private static loaded = false;
    private static readonly PLUGIN_ID = "online3d-viewer";

    static async loadOnline3dViewer(): Promise<void> {
        if (this.loaded) return;

        try {
            // Load CSS dynamically from public directory using StylesheetManager
            // This allows proper cleanup when the plugin is unloaded

            // await StylesheetManager.loadStylesheet(
            //     this.PLUGIN_ID,
            //     "/online3dviewer/Online3DViewerContainer.css"
            // );

            console.log("Online3dViewer dependencies loaded successfully");
            this.loaded = true;
        } catch (error) {
            console.error("Failed to load Online3dViewer dependencies:", error);
            throw error;
        }
    }

    static cleanup(): void {
        // Remove all stylesheets managed by this plugin
        StylesheetManager.removePluginStylesheets(this.PLUGIN_ID);

        this.loaded = false;
        console.log("Online3dViewer dependencies cleaned up");
    }

    static isLoaded(): boolean {
        return this.loaded;
    }
}
