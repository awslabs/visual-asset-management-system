/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Viewer manifest - clean constants for webpack static analysis
// This file tells webpack which components to include in the build

// Viewer component paths - add new viewers here
export const VIEWER_COMPONENTS = {
    "./viewers/ImageViewerPlugin/ImageViewerComponent": "ImageViewerPlugin/ImageViewerComponent",
    "./viewers/Online3dViewerPlugin/Online3dViewerComponent":
        "Online3dViewerPlugin/Online3dViewerComponent",
    "./viewers/VideoViewerPlugin/VideoViewerComponent": "VideoViewerPlugin/VideoViewerComponent",
    "./viewers/AudioViewerPlugin/AudioViewerComponent": "AudioViewerPlugin/AudioViewerComponent",
    "./viewers/HTMLViewerPlugin/HTMLViewerComponent": "HTMLViewerPlugin/HTMLViewerComponent",
    "./viewers/PotreeViewerPlugin/PotreeViewerComponent":
        "PotreeViewerPlugin/PotreeViewerComponent",
    "./viewers/ThreeDimensionalPlotterPlugin/ThreeDimensionalPlotterComponent":
        "ThreeDimensionalPlotterPlugin/ThreeDimensionalPlotterComponent",
    "./viewers/ColumnarViewerPlugin/ColumnarViewerComponent":
        "ColumnarViewerPlugin/ColumnarViewerComponent",
    "./viewers/PDFViewerPlugin/PDFViewerComponent": "PDFViewerPlugin/PDFViewerComponent",
    "./viewers/CesiumViewerPlugin/CesiumViewerComponent":
        "CesiumViewerPlugin/CesiumViewerComponent",
    "./viewers/TextViewerPlugin/TextViewerComponent": "TextViewerPlugin/TextViewerComponent",
} as const;

// Dependency manager paths - add new dependency managers here
export const DEPENDENCY_MANAGERS = {
    "./viewers/PotreeViewerPlugin/dependencies": "PotreeViewerPlugin/dependencies",
    "./viewers/CesiumViewerPlugin/dependencies": "CesiumViewerPlugin/dependencies",
    // Add new dependency managers here as needed:
    // './viewers/MyViewerPlugin/dependencies': 'MyViewerPlugin/dependencies',
} as const;

// Instructions for adding new viewers:
// 1. Create the component file: MyViewerPlugin/MyViewerComponent.tsx
// 2. Add entry to VIEWER_COMPONENTS above
// 3. Add entry to DEPENDENCY_MANAGERS above (if needed)
// 4. Add configuration to viewerConfig.json
// 5. No changes needed to PluginRegistry.ts!
