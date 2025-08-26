/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

interface LoadingOverlayProps {
    scriptsLoaded: boolean;
    viewerInitialized: boolean;
    assetsLoaded: boolean;
}

export const LoadingOverlay: React.FC<LoadingOverlayProps> = ({
    scriptsLoaded,
    viewerInitialized,
    assetsLoaded,
}) => {
    return (
        <div className="ov-loading-overlay">
            <div className="ov-loading-content">
                <div className="ov-loading-title">Loading Online 3D Viewer...</div>
                <div className="ov-loading-status">
                    <div className="ov-loading-item">
                        <span
                            className={`ov-loading-icon ${scriptsLoaded ? "completed" : "loading"}`}
                        >
                            {scriptsLoaded ? "✓" : "⏳"}
                        </span>
                        Scripts
                    </div>
                    <div className="ov-loading-item">
                        <span
                            className={`ov-loading-icon ${
                                viewerInitialized ? "completed" : "loading"
                            }`}
                        >
                            {viewerInitialized ? "✓" : "⏳"}
                        </span>
                        Viewer
                    </div>
                    <div className="ov-loading-item">
                        <span
                            className={`ov-loading-icon ${assetsLoaded ? "completed" : "loading"}`}
                        >
                            {assetsLoaded ? "✓" : "⏳"}
                        </span>
                        Assets
                    </div>
                </div>
            </div>
        </div>
    );
};
