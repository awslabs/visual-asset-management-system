/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import { PluginRegistry } from "./PluginRegistry";

/**
 * Ensures the viewer registry is initialized and re-renders the caller when it is.
 *
 * Any surface that decides whether to OFFER a viewer (an eye icon, a "view" action) needs this.
 * Until `initialize()` has run, `getCompatibleViewers` returns an empty list, so a viewability check
 * reports "nothing can render this" for every file and the control is hidden everywhere. The two
 * existing initializers do not cover that case: `DynamicViewer` initializes when a viewer is already
 * being opened, which is after the offer has been made, and the `visualizerPlugin` barrel
 * self-initializes on import but only the search page imports it.
 *
 * Returns false on the first render and true once the registry is ready, which is what drives the
 * re-render that reveals the control.
 */
export function useViewerRegistryReady(): boolean {
    const [ready, setReady] = useState(() => PluginRegistry.getInstance().isInitialized());

    useEffect(() => {
        if (ready) return;
        let cancelled = false;

        const initialize = async () => {
            try {
                const registry = PluginRegistry.getInstance();
                if (!registry.isInitialized()) {
                    await registry.initialize();
                }
                if (!cancelled) setReady(true);
            } catch (error) {
                // A failure here only means no viewer entry points are offered, which is the same
                // outcome as having no compatible viewer — nothing to surface to the user.
                console.error("Failed to initialize the viewer plugin registry:", error);
            }
        };

        initialize();
        return () => {
            cancelled = true;
        };
    }, [ready]);

    return ready;
}
