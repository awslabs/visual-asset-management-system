/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import { IfcViewerInstance } from "../types";
import { CameraView, fitCameraToModels, setCameraView, zoomCamera } from "../utils/ifcLoader";
import styles from "../ThatOpenWebIfcPanel.module.css";

interface ToolsProps {
    instance: IfcViewerInstance;
    bundle: any;
}

/** Camera viewpoint presets shown as a grid. */
const VIEWS: { id: CameraView; label: string }[] = [
    { id: "top", label: "Top" },
    { id: "front", label: "Front" },
    { id: "back", label: "Back" },
    { id: "left", label: "Left" },
    { id: "right", label: "Right" },
    { id: "iso", label: "Iso" },
];

/** The placement tool that currently owns the double-click gesture. */
type ActiveTool = "none" | "section" | "length" | "area";

/**
 * BIM tools: camera control (viewpoint presets, zoom, fit), clipping / section
 * planes, and length & area measurements.
 *
 * Section / Length / Area are MUTUALLY EXCLUSIVE: only one can be active at a
 * time, and a single double-click handler routes the gesture to whichever is
 * active. This avoids the previous bug where enabling section + a measurement
 * made one double-click both cut a plane and start a measurement.
 *
 * Verified That Open API (v3.4):
 *   - Clipper.create(world)  — section planes take the world arg.
 *   - LengthMeasurement / AreaMeasurement: set .world, .enabled, .snappings,
 *     then create()  — measurements take NO args; the cursor position is read
 *     internally. Clear all with measurement.list.clear() (there is no
 *     deleteAll() on measurements — that was the reason "Clear" did nothing).
 *
 * While a placement tool is active we also disable the Highlighter so a
 * double-click doesn't simultaneously select an element.
 */
const Tools: React.FC<ToolsProps> = ({ instance, bundle }) => {
    const { OBC, OBF, FRAGS } = bundle;
    const [activeTool, setActiveTool] = useState<ActiveTool>("none");

    // Lazily-resolved That Open components (resolved once, reused).
    const clipperRef = useRef<any>(null);
    const lengthRef = useRef<any>(null);
    const areaRef = useRef<any>(null);
    // Keep the latest activeTool readable inside the dblclick listener without
    // re-subscribing on every change.
    const activeToolRef = useRef<ActiveTool>("none");
    useEffect(() => {
        activeToolRef.current = activeTool;
    }, [activeTool]);

    const getClipper = () => {
        if (!clipperRef.current) clipperRef.current = instance.components.get(OBC.Clipper);
        return clipperRef.current;
    };
    const getLength = () => {
        if (!lengthRef.current) {
            const m = instance.components.get(OBF.LengthMeasurement);
            m.world = instance.world;
            if (FRAGS?.SnappingClass) m.snappings = [FRAGS.SnappingClass.POINT];
            lengthRef.current = m;
        }
        return lengthRef.current;
    };
    const getArea = () => {
        if (!areaRef.current) {
            const m = instance.components.get(OBF.AreaMeasurement);
            m.world = instance.world;
            if (FRAGS?.SnappingClass) m.snappings = [FRAGS.SnappingClass.POINT];
            areaRef.current = m;
        }
        return areaRef.current;
    };

    // One double-click handler routes to whichever placement tool is active.
    useEffect(() => {
        const dom: HTMLElement | undefined = instance.world?.renderer?.three?.domElement;
        if (!dom) return;

        const onDblClick = () => {
            try {
                switch (activeToolRef.current) {
                    case "section":
                        getClipper().create(instance.world);
                        break;
                    case "length":
                        getLength().create();
                        break;
                    case "area":
                        getArea().create();
                        break;
                    default:
                        break;
                }
            } catch (err) {
                console.warn("ThatOpenWebIfc: tool create failed:", err);
            }
        };

        dom.addEventListener("dblclick", onDblClick);
        return () => dom.removeEventListener("dblclick", onDblClick);
        // Bind once per renderer DOM element. The get* helpers are stable
        // ref-backed resolvers and the active tool is read via activeToolRef, so
        // they intentionally do not belong in the dependency array (re-binding
        // the listener on every render would be wrong).
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [instance]);

    // On unmount (e.g. switching tabs), leave the viewer in a clean state:
    // disable any active placement tool and re-enable click-selection. Otherwise
    // a tool enabled on the Tools tab would stay armed with selection disabled
    // after navigating away, with no double-click handler to drive it.
    useEffect(() => {
        return () => {
            try {
                if (clipperRef.current) clipperRef.current.enabled = false;
                if (lengthRef.current) {
                    lengthRef.current.enabled = false;
                    lengthRef.current.cancelCreation?.();
                }
                if (areaRef.current) {
                    areaRef.current.enabled = false;
                    areaRef.current.cancelCreation?.();
                }
                if (instance.highlighter) instance.highlighter.enabled = true;
            } catch (err) {
                console.warn("ThatOpenWebIfc: tools cleanup failed:", err);
            }
        };
    }, [instance]);

    // Switch the active placement tool, disabling the others. Passing the same
    // tool again toggles it off.
    const selectTool = (tool: ActiveTool) => {
        const next = activeTool === tool ? "none" : tool;

        // Configure each tool's enabled state to match the new selection.
        try {
            getClipper().enabled = next === "section";
        } catch (err) {
            console.warn("ThatOpenWebIfc: clipper enable failed:", err);
        }
        try {
            const len = getLength();
            len.enabled = next === "length";
            if (next !== "length") len.cancelCreation?.();
        } catch (err) {
            console.warn("ThatOpenWebIfc: length enable failed:", err);
        }
        try {
            const ar = getArea();
            ar.enabled = next === "area";
            if (next !== "area") ar.cancelCreation?.();
        } catch (err) {
            console.warn("ThatOpenWebIfc: area enable failed:", err);
        }

        // Disable click-selection while a placement tool is active so a
        // double-click doesn't also select an element.
        try {
            if (instance.highlighter) instance.highlighter.enabled = next === "none";
        } catch (err) {
            console.warn("ThatOpenWebIfc: highlighter toggle failed:", err);
        }

        setActiveTool(next);
    };

    // ---- Camera ----
    const goToView = async (view: CameraView) => {
        try {
            await setCameraView(bundle, instance.components, instance.world, view);
        } catch (err) {
            console.warn("ThatOpenWebIfc: setCameraView failed:", err);
        }
    };

    const fit = async () => {
        try {
            await fitCameraToModels(bundle, instance.components, instance.world);
        } catch (err) {
            console.warn("ThatOpenWebIfc: fit failed:", err);
        }
    };

    const zoom = (direction: number) => {
        try {
            zoomCamera(bundle, instance.components, instance.world, direction);
        } catch (err) {
            console.warn("ThatOpenWebIfc: zoom failed:", err);
        }
    };

    // Finish the in-progress AREA polygon. endCreation() closes it as long as
    // there are >= 3 points. (Length needs no finish: it auto-completes when the
    // second point is placed.)
    const finishArea = () => {
        try {
            getArea().endCreation?.();
        } catch (err) {
            console.warn("ThatOpenWebIfc: finish area failed:", err);
        }
    };

    // Cancel (discard) the in-progress AREA polygon.
    const cancelArea = () => {
        try {
            getArea().cancelCreation?.();
        } catch (err) {
            console.warn("ThatOpenWebIfc: cancel area failed:", err);
        }
    };

    // Keyboard shortcuts while the AREA tool is active: Enter finishes the
    // polygon, Escape cancels it. Bound at the window so it works while the
    // cursor is over the 3D canvas. Ignores typing in inputs. Length needs no
    // finish gesture, so these only apply to area.
    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if (activeToolRef.current !== "area") return;
            if (
                event.target instanceof HTMLInputElement ||
                event.target instanceof HTMLTextAreaElement
            ) {
                return;
            }
            if (event.key === "Enter") {
                event.preventDefault();
                finishArea();
            } else if (event.key === "Escape") {
                // Stop the parent panel's Escape-to-hide while measuring.
                event.preventDefault();
                event.stopPropagation();
                cancelArea();
            }
        };
        // Capture phase so our Escape handling runs before the panel's.
        window.addEventListener("keydown", onKeyDown, true);
        return () => window.removeEventListener("keydown", onKeyDown, true);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [instance]);

    // ---- Section planes ----
    const deleteClips = () => {
        try {
            getClipper().deleteAll();
        } catch (err) {
            console.warn("ThatOpenWebIfc: delete clips failed:", err);
        }
    };

    // ---- Measurements ----
    // Clearing must (1) resolve the measurement component even if the user never
    // toggled it this render, (2) cancel any in-progress measurement (an
    // unfinished area is not yet in `list`, so clearing the list alone would
    // leave its preview lines on screen), then (3) clear the list — whose
    // onCleared cascade disposes the lines, fills and labels.
    const clearLength = () => {
        try {
            const m = getLength();
            m.cancelCreation?.();
            m.list?.clear?.();
        } catch (err) {
            console.warn("ThatOpenWebIfc: clear length failed:", err);
        }
    };
    const clearArea = () => {
        try {
            const m = getArea();
            m.cancelCreation?.();
            m.list?.clear?.();
        } catch (err) {
            console.warn("ThatOpenWebIfc: clear area failed:", err);
        }
    };
    const clearMeasurements = () => {
        clearLength();
        clearArea();
    };

    return (
        <div>
            {/* Camera viewpoints */}
            <div className={styles.section}>
                <div className={styles.sectionHeader}>📷 Camera Views</div>
                <div className={styles.grid3}>
                    {VIEWS.map((v) => (
                        <button
                            key={v.id}
                            className={styles.smallButton}
                            onClick={() => goToView(v.id)}
                            title={`${v.label} view`}
                        >
                            {v.label}
                        </button>
                    ))}
                </div>
            </div>

            {/* Quick camera actions */}
            <div className={styles.section}>
                <div className={styles.sectionHeader}>🎯 Quick Actions</div>
                <button
                    className={styles.buttonPrimary}
                    onClick={fit}
                    title="Frame the whole model"
                >
                    🎯 Fit to View
                </button>
                <div className={styles.grid2}>
                    <button className={styles.button} onClick={() => zoom(1)} title="Zoom in">
                        ＋ Zoom In
                    </button>
                    <button className={styles.button} onClick={() => zoom(-1)} title="Zoom out">
                        － Zoom Out
                    </button>
                </div>
            </div>

            {/* Section planes */}
            <div className={styles.section}>
                <div className={styles.sectionHeader}>✂️ Section Planes</div>
                <button
                    className={activeTool === "section" ? styles.buttonActive : styles.button}
                    onClick={() => selectTool("section")}
                >
                    {activeTool === "section" ? "Section: ON" : "Enable Section Plane"}
                </button>
                <button className={styles.buttonWarn} onClick={deleteClips}>
                    Delete All Planes
                </button>
            </div>

            {/* Measurements */}
            <div className={styles.section}>
                <div className={styles.sectionHeader}>📐 Measure</div>
                <div className={styles.grid2}>
                    <button
                        className={activeTool === "length" ? styles.buttonActive : styles.button}
                        onClick={() => selectTool("length")}
                    >
                        Length
                    </button>
                    <button
                        className={activeTool === "area" ? styles.buttonActive : styles.button}
                        onClick={() => selectTool("area")}
                    >
                        Area
                    </button>
                </div>
                {/* Finish / Cancel for an in-progress AREA polygon. Length needs
                    no finish — it completes when the second point is placed. */}
                {activeTool === "area" && (
                    <div className={styles.grid2}>
                        <button
                            className={styles.buttonPrimary}
                            onClick={finishArea}
                            title="Finish the area (Enter)"
                        >
                            ✓ Finish Area
                        </button>
                        <button
                            className={styles.button}
                            onClick={cancelArea}
                            title="Cancel the area (Esc)"
                        >
                            ✖ Cancel
                        </button>
                    </div>
                )}
                <button className={styles.buttonWarn} onClick={clearMeasurements}>
                    Clear Measurements
                </button>
            </div>

            {/* Contextual help for the active placement tool */}
            {activeTool !== "none" && (
                <div className={styles.section}>
                    <div className={styles.hint}>
                        {activeTool === "section" &&
                            "Double-click a surface to place a section plane. Delete/Backspace removes the one under the cursor."}
                        {activeTool === "length" &&
                            "Double-click two points to measure the distance between them."}
                        {activeTool === "area" &&
                            "Double-click points around a region (3 or more), then Finish (or Enter) to close the area. Esc cancels."}
                    </div>
                </div>
            )}

            {/* Navigation help */}
            <div className={styles.section} style={{ marginBottom: 0 }}>
                <div className={styles.sectionHeader}>🖱 Navigation</div>
                <div className={styles.hint}>
                    Left drag: orbit · Right drag: pan · Wheel: zoom · Click: select
                </div>
            </div>
        </div>
    );
};

export default Tools;
