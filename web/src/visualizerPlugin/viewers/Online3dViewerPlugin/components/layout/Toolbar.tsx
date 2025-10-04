/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useViewerContext } from "../../context/ViewerContext";
import { MeasureTool } from "../tools/MeasureTool";

export const Toolbar: React.FC = () => {
    const { state, settings, cameraSettings, updateCameraSettings, switchTheme, fitModelToWindow } =
        useViewerContext();

    const [measureToolActive, setMeasureToolActive] = useState(false);

    const getUnderlyingViewer = () => {
        if (state.viewer) {
            // If viewer has both embeddedViewer and viewer properties
            if (state.viewer.viewer) {
                return state.viewer.viewer;
            }
            // If viewer has GetViewer method (is EmbeddedViewer)
            if (state.viewer.GetViewer) {
                return state.viewer.GetViewer();
            }
            // Fallback - viewer might be the underlying viewer itself
            return state.viewer;
        }
        return null;
    };

    const handleFitToWindow = () => {
        const viewer = getUnderlyingViewer();
        if (viewer && state.model) {
            try {
                const boundingSphere = viewer.GetBoundingSphere(() => true);
                if (boundingSphere) {
                    viewer.FitSphereToWindow(boundingSphere, true);
                }
            } catch (error) {
                console.error("Error fitting model to window:", error);
            }
        }
    };

    const handleSetUpVector = async (direction: "Y" | "Z") => {
        const viewer = getUnderlyingViewer();
        if (viewer) {
            try {
                const OV = await import("online-3d-viewer");
                const directionEnum = direction === "Y" ? OV.Direction.Y : OV.Direction.Z;
                viewer.SetUpVector(directionEnum, true);
            } catch (error) {
                console.error("Error setting up vector:", error);
            }
        }
    };

    const handleFlipUpVector = () => {
        const viewer = getUnderlyingViewer();
        if (viewer) {
            try {
                viewer.FlipUpVector();
            } catch (error) {
                console.error("Error flipping up vector:", error);
            }
        }
    };

    const handleNavigationModeChange = async (mode: "FixedUpVector" | "FreeOrbit") => {
        const viewer = getUnderlyingViewer();
        if (viewer) {
            try {
                const OV = await import("online-3d-viewer");
                const navMode =
                    mode === "FixedUpVector"
                        ? OV.NavigationMode.FixedUpVector
                        : OV.NavigationMode.FreeOrbit;
                viewer.SetNavigationMode(navMode);
            } catch (error) {
                console.error("Error setting navigation mode:", error);
            }
        }
        updateCameraSettings({ navigationMode: mode });
    };

    const handleProjectionModeChange = async (mode: "Perspective" | "Orthographic") => {
        const viewer = getUnderlyingViewer();
        if (viewer) {
            try {
                const OV = await import("online-3d-viewer");
                const projMode =
                    mode === "Perspective"
                        ? OV.ProjectionMode.Perspective
                        : OV.ProjectionMode.Orthographic;
                viewer.SetProjectionMode(projMode);
            } catch (error) {
                console.error("Error setting projection mode:", error);
            }
        }
        updateCameraSettings({ projectionMode: mode });
    };

    const handleThemeToggle = () => {
        switchTheme(settings.themeId === "light" ? "dark" : "light");
    };

    const handleMeasureToolToggle = () => {
        setMeasureToolActive(!measureToolActive);
    };

    return (
        <>
            <div className="ov-toolbar">
                {/* File operations removed - no longer needed */}

                {/* Model operations - only show when model is loaded */}
                <div
                    className="ov_toolbar_button only_on_model"
                    onClick={handleFitToWindow}
                    title="Fit model to window"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-fit"></i>
                    </div>
                </div>

                <div
                    className="ov_toolbar_button only_on_model"
                    onClick={() => handleSetUpVector("Y")}
                    title="Set Y axis as up vector"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-up_y"></i>
                    </div>
                </div>

                <div
                    className="ov_toolbar_button only_on_model"
                    onClick={() => handleSetUpVector("Z")}
                    title="Set Z axis as up vector"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-up_z"></i>
                    </div>
                </div>

                <div
                    className="ov_toolbar_button only_on_model"
                    onClick={handleFlipUpVector}
                    title="Flip up vector"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-flip"></i>
                    </div>
                </div>

                <div className="ov_toolbar_separator only_full_width only_on_model"></div>

                {/* Navigation mode */}
                <div
                    className={`ov_toolbar_button only_full_width only_on_model ${
                        cameraSettings.navigationMode === "FixedUpVector" ? "selected" : ""
                    }`}
                    onClick={() => handleNavigationModeChange("FixedUpVector")}
                    title="Fixed up vector"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-fix_up_on"></i>
                    </div>
                </div>
                <div
                    className={`ov_toolbar_button only_full_width only_on_model ${
                        cameraSettings.navigationMode === "FreeOrbit" ? "selected" : ""
                    }`}
                    onClick={() => handleNavigationModeChange("FreeOrbit")}
                    title="Free orbit"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-fix_up_off"></i>
                    </div>
                </div>

                <div className="ov_toolbar_separator only_full_width only_on_model"></div>

                {/* Projection mode */}
                <div
                    className={`ov_toolbar_button only_full_width only_on_model ${
                        cameraSettings.projectionMode === "Perspective" ? "selected" : ""
                    }`}
                    onClick={() => handleProjectionModeChange("Perspective")}
                    title="Perspective camera"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-camera_perspective"></i>
                    </div>
                </div>
                <div
                    className={`ov_toolbar_button only_full_width only_on_model ${
                        cameraSettings.projectionMode === "Orthographic" ? "selected" : ""
                    }`}
                    onClick={() => handleProjectionModeChange("Orthographic")}
                    title="Orthographic camera"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-camera_orthographic"></i>
                    </div>
                </div>

                <div className="ov_toolbar_separator only_full_width only_on_model"></div>

                {/* Measure Tool */}
                <div
                    className={`ov_toolbar_button only_full_width only_on_model ${
                        measureToolActive ? "selected" : ""
                    }`}
                    onClick={handleMeasureToolToggle}
                    title="Measure"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-measure"></i>
                    </div>
                </div>

                {/* Theme toggle - always visible */}
                <div
                    className="ov_toolbar_button align_right"
                    onClick={handleThemeToggle}
                    title={
                        settings.themeId === "light"
                            ? "Switch to dark mode"
                            : "Switch to light mode"
                    }
                >
                    <div className="ov_svg_icon">
                        <i
                            className={
                                settings.themeId === "light" ? "icon-dark_mode" : "icon-light_mode"
                            }
                        ></i>
                    </div>
                </div>
            </div>

            {/* Measure Tool Component */}
            <MeasureTool isActive={measureToolActive} onToggle={setMeasureToolActive} />
        </>
    );
};
