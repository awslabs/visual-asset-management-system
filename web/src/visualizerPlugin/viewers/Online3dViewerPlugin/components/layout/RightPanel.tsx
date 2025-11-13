/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useCallback } from "react";
import { useViewerContext } from "../../context/ViewerContext";
import { ModelDetailsDisplay } from "../panels/ModelDetailsDisplay";

interface RightPanelProps {
    contentWidth?: number;
}

export const RightPanel: React.FC<RightPanelProps> = ({ contentWidth = 280 }) => {
    const { state, settings, updateSettings, selection } = useViewerContext();
    const [activeTab, setActiveTab] = useState<"details" | "settings">("details");
    const [isVisible, setIsVisible] = useState(true);

    const toggleVisibility = () => {
        setIsVisible(!isVisible);
    };

    const handleTabClick = (tab: "details" | "settings") => {
        if (activeTab === tab && isVisible) {
            // If clicking the same tab while visible, hide the panel
            setIsVisible(false);
        } else {
            // Show panel and switch to tab
            setIsVisible(true);
            setActiveTab(tab);
        }
    };

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

    const getOVLibrary = () => {
        // Try to get OV from the viewer wrapper first
        if (state.viewer && state.viewer.OV) {
            return state.viewer.OV;
        }
        // Fallback to global window.OV
        if (window.OV) {
            return window.OV;
        }
        return null;
    };

    const handleBackgroundColorChange = useCallback(
        async (color: string) => {
            const r = parseInt(color.slice(1, 3), 16);
            const g = parseInt(color.slice(3, 5), 16);
            const b = parseInt(color.slice(5, 7), 16);

            // Update settings first
            updateSettings({ backgroundColor: { r, g, b } });

            // Apply to viewer immediately
            const viewer = getUnderlyingViewer();
            const OV = getOVLibrary();

            if (viewer && OV) {
                try {
                    // Create RGBAColor object with alpha channel
                    const rgbaColor = new OV.RGBAColor(r, g, b, 255);
                    viewer.SetBackgroundColor(rgbaColor);
                    viewer.Render();
                    console.log("Background color updated successfully:", { r, g, b });
                } catch (error) {
                    console.error("Error setting background color:", error);
                }
            } else {
                console.warn("Viewer or OV library not available for background color change");
            }
        },
        [updateSettings]
    );

    const handleEdgeToggle = useCallback(async () => {
        const newShowEdges = !settings.showEdges;

        // Apply to viewer immediately
        const viewer = getUnderlyingViewer();
        if (viewer) {
            try {
                if (viewer.SetEdgeSettings) {
                    // Create EdgeSettings object
                    let edgeSettings;
                    if (window.OV && window.OV.EdgeSettings) {
                        const edgeColor = window.OV.RGBColor
                            ? new window.OV.RGBColor(
                                  settings.edgeSettings.edgeColor.r,
                                  settings.edgeSettings.edgeColor.g,
                                  settings.edgeSettings.edgeColor.b
                              )
                            : settings.edgeSettings.edgeColor;
                        edgeSettings = new window.OV.EdgeSettings(
                            newShowEdges,
                            edgeColor,
                            settings.edgeSettings.edgeThreshold
                        );
                    } else {
                        // Fallback - create simple settings object
                        edgeSettings = {
                            showEdges: newShowEdges,
                            edgeColor: settings.edgeSettings.edgeColor,
                            edgeThreshold: settings.edgeSettings.edgeThreshold,
                        };
                    }
                    viewer.SetEdgeSettings(edgeSettings);
                    viewer.Render();
                }
            } catch (error) {
                console.error("Error setting edge settings:", error);
            }
        }

        updateSettings({
            showEdges: newShowEdges,
            edgeSettings: {
                ...settings.edgeSettings,
                showEdges: newShowEdges,
            },
        });
    }, [settings, updateSettings]);

    const handleEnvironmentMapChange = useCallback(
        (envMapName: string) => {
            const viewer = getUnderlyingViewer();
            if (viewer) {
                try {
                    if (viewer.SetEnvironmentMapSettings) {
                        // Create environment map settings with correct path
                        const envMapPath = `/viewers/online3dviewer/assets/envmaps/${envMapName}/`;
                        const envMapTextures = [
                            envMapPath + "posx.jpg",
                            envMapPath + "negx.jpg",
                            envMapPath + "posy.jpg",
                            envMapPath + "negy.jpg",
                            envMapPath + "posz.jpg",
                            envMapPath + "negz.jpg",
                        ];

                        let environmentSettings;
                        if (window.OV && window.OV.EnvironmentSettings) {
                            environmentSettings = new window.OV.EnvironmentSettings(
                                envMapTextures,
                                settings.backgroundIsEnvMap
                            );
                        } else {
                            environmentSettings = {
                                textures: envMapTextures,
                                backgroundIsEnvMap: settings.backgroundIsEnvMap,
                            };
                        }

                        viewer.SetEnvironmentMapSettings(environmentSettings);
                        viewer.Render();
                        console.log("Environment map updated:", envMapName, envMapTextures);
                    }
                } catch (error) {
                    console.error("Error setting environment map:", error);
                }
            }

            updateSettings({ environmentMapName: envMapName });
        },
        [settings.backgroundIsEnvMap, updateSettings]
    );

    const rgbToHex = (r: number, g: number, b: number) => {
        return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
    };

    if (!isVisible) {
        return (
            <div className="ov-right-panel-collapsed">
                <button
                    className="ov-panel-toggle-button"
                    onClick={toggleVisibility}
                    title="Show sidebar"
                >
                    ◀
                </button>
            </div>
        );
    }

    return (
        <div className="ov-right-panel ov_panel_set_right_container">
            <div className="ov_panel_set_content">
                {activeTab === "details" && (
                    <div className="ov-details-panel">
                        {state.model ? (
                            <ModelDetailsDisplay model={state.model} selection={selection} />
                        ) : (
                            <div className="ov-empty-state">
                                <p>No model loaded</p>
                            </div>
                        )}
                    </div>
                )}

                {activeTab === "settings" && (
                    <div className="ov-settings-panel">
                        <div className="ov-panel-section">
                            <h4>Appearance</h4>

                            <div className="ov_property_table">
                                <div className="ov_property_table_row">
                                    <div className="ov_property_table_name">Background Color:</div>
                                    <div className="ov_property_table_value">
                                        <input
                                            type="color"
                                            value={rgbToHex(
                                                settings.backgroundColor.r,
                                                settings.backgroundColor.g,
                                                settings.backgroundColor.b
                                            )}
                                            onChange={(e) =>
                                                handleBackgroundColorChange(e.target.value)
                                            }
                                        />
                                    </div>
                                </div>

                                <div className="ov_property_table_row">
                                    <div className="ov_property_table_name">Show Edges:</div>
                                    <div className="ov_property_table_value">
                                        <input
                                            type="checkbox"
                                            checked={settings.showEdges}
                                            onChange={handleEdgeToggle}
                                        />
                                    </div>
                                </div>

                                <div className="ov_property_table_row">
                                    <div className="ov_property_table_name">Environment Map:</div>
                                    <div className="ov_property_table_value">
                                        <select
                                            value={settings.environmentMapName}
                                            onChange={(e) =>
                                                handleEnvironmentMapChange(e.target.value)
                                            }
                                        >
                                            <option value="fishermans_bastion">
                                                Fisherman's Bastion
                                            </option>
                                            <option value="citadella">Citadella</option>
                                            <option value="ice_river">Ice River</option>
                                            <option value="maskonaive">Maskonaive</option>
                                            <option value="park">Park</option>
                                            <option value="teide">Teide</option>
                                        </select>
                                    </div>
                                </div>

                                <div className="ov_property_table_row">
                                    <div className="ov_property_table_name">
                                        Environment Background:
                                    </div>
                                    <div className="ov_property_table_value">
                                        <input
                                            type="checkbox"
                                            checked={settings.backgroundIsEnvMap}
                                            onChange={(e) => {
                                                const newBackgroundIsEnvMap = e.target.checked;
                                                updateSettings({
                                                    backgroundIsEnvMap: newBackgroundIsEnvMap,
                                                });

                                                // Apply to viewer immediately
                                                const viewer = getUnderlyingViewer();
                                                const OV = getOVLibrary();

                                                if (viewer && OV) {
                                                    try {
                                                        const envMapPath = `/viewers/online3dviewer/assets/envmaps/${settings.environmentMapName}/`;
                                                        const envMapTextures = [
                                                            envMapPath + "posx.jpg",
                                                            envMapPath + "negx.jpg",
                                                            envMapPath + "posy.jpg",
                                                            envMapPath + "negy.jpg",
                                                            envMapPath + "posz.jpg",
                                                            envMapPath + "negz.jpg",
                                                        ];

                                                        let environmentSettings;
                                                        if (OV.EnvironmentSettings) {
                                                            environmentSettings =
                                                                new OV.EnvironmentSettings(
                                                                    envMapTextures,
                                                                    newBackgroundIsEnvMap
                                                                );
                                                        } else {
                                                            environmentSettings = {
                                                                textures: envMapTextures,
                                                                backgroundIsEnvMap:
                                                                    newBackgroundIsEnvMap,
                                                            };
                                                        }

                                                        if (viewer.SetEnvironmentMapSettings) {
                                                            viewer.SetEnvironmentMapSettings(
                                                                environmentSettings
                                                            );
                                                            viewer.Render();
                                                            console.log(
                                                                "Environment background toggled:",
                                                                newBackgroundIsEnvMap
                                                            );
                                                        }
                                                    } catch (error) {
                                                        console.error(
                                                            "Error toggling environment background:",
                                                            error
                                                        );
                                                    }
                                                }
                                            }}
                                        />
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="ov-panel-section">
                            <h4>Theme</h4>
                            <div className="ov_property_table">
                                <div className="ov_property_table_row">
                                    <div className="ov_property_table_name">Current Theme:</div>
                                    <div className="ov_property_table_value">
                                        {settings.themeId === "light" ? "☀️ Light" : "🌙 Dark"}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>

            <div className="ov_panel_set_menu">
                <div
                    className={`ov_panel_set_menu_button ${
                        activeTab === "details" ? "selected" : ""
                    }`}
                    onClick={() => handleTabClick("details")}
                    title="Details"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-details"></i>
                    </div>
                </div>
                <div
                    className={`ov_panel_set_menu_button ${
                        activeTab === "settings" ? "selected" : ""
                    }`}
                    onClick={() => handleTabClick("settings")}
                    title="Settings"
                >
                    <div className="ov_svg_icon">
                        <i className="icon-settings"></i>
                    </div>
                </div>
            </div>
        </div>
    );
};
