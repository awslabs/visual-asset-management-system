/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Type declarations for @vntana/viewer
 * This helps TypeScript resolve the module when using dynamic imports
 */

declare module "@vntana/viewer" {
    // The module exports web components that register themselves
    // No specific exports need to be typed as we use the custom elements directly
    const vntanaViewer: any;
    export default vntanaViewer;
}

declare module "@vntana/viewer/core" {
    const vntanaViewerCore: any;
    export default vntanaViewerCore;
}

declare module "@vntana/viewer/hotspot" {
    const vntanaViewerHotspot: any;
    export default vntanaViewerHotspot;
}

declare module "@vntana/viewer/ui" {
    const vntanaViewerUI: any;
    export default vntanaViewerUI;
}

declare module "@vntana/viewer/ui/*" {
    const vntanaViewerUIComponent: any;
    export default vntanaViewerUIComponent;
}

declare module "@vntana/viewer/styles/viewer.css" {
    const styles: any;
    export default styles;
}
