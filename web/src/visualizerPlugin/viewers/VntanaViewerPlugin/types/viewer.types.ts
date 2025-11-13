/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ViewerPluginProps } from "../../../core/types";

/**
 * Props for the VNTANA Viewer Component
 * Extends the base ViewerPluginProps with VNTANA-specific properties
 */
export interface VntanaViewerProps extends ViewerPluginProps {
    assetId: string;
    databaseId: string;
    assetKey?: string;
    versionId?: string;
}

/**
 * VNTANA Viewer custom element interface
 * Represents the <vntana-viewer> web component
 */
export interface VntanaViewerElement extends HTMLElement {
    src: string;
    usdzSrc?: string;
    environmentSrc?: string;
    shadowIntensity?: number;
    shadowRadius?: number;
    enableAutoRotate?: boolean;
    autoRotateSpeed?: string;
    fieldOfView?: string;
    cameraRotation?: string;
    exposure?: number;
    toneMapping?: string;
    antiAliasing?: string;
    background?: string;
    loading?: string;
    poster?: string;
}

/**
 * VNTANA Viewer load event detail
 */
export interface VntanaLoadEvent extends Event {
    detail: {
        time: number;
    };
}

/**
 * VNTANA Viewer error event
 */
export interface VntanaErrorEvent extends Event {
    detail?: {
        message?: string;
    };
}
