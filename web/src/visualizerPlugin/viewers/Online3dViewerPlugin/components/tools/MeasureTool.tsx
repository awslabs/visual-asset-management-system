/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState, useCallback } from "react";
import { useViewerContext } from "../../context/ViewerContext";

interface MeasureToolProps {
    isActive: boolean;
    onToggle: (active: boolean) => void;
}

interface Marker {
    intersection: any;
    markerObject: any;
}

interface MeasureValues {
    pointsDistance: number | null;
    parallelFacesDistance: number | null;
    facesAngle: number | null;
}

export const MeasureTool: React.FC<MeasureToolProps> = ({ isActive, onToggle }) => {
    const { state, settings } = useViewerContext();
    const [markers, setMarkers] = useState<Marker[]>([]);
    const [tempMarker, setTempMarker] = useState<Marker | null>(null);
    const [measureValues, setMeasureValues] = useState<MeasureValues | null>(null);
    const panelRef = useRef<HTMLDivElement>(null);
    const mouseHandlerRef = useRef<((event: MouseEvent) => void) | null>(null);
    const mouseMoveHandlerRef = useRef<((event: MouseEvent) => void) | null>(null);

    const getUnderlyingViewer = useCallback(() => {
        if (state.viewer) {
            if (state.viewer.viewer) {
                return state.viewer.viewer;
            }
            if (state.viewer.GetViewer) {
                return state.viewer.GetViewer();
            }
            return state.viewer;
        }
        return null;
    }, [state.viewer]);

    const getOVLibrary = useCallback(() => {
        if (state.viewer && state.viewer.OV) {
            return state.viewer.OV;
        }
        if (window.OV) {
            return window.OV;
        }
        return null;
    }, [state.viewer]);

    const createMaterial = useCallback(() => {
        const OV = getOVLibrary();
        if (OV && OV.THREE) {
            return new OV.THREE.LineBasicMaterial({
                color: 0x263238,
                depthTest: false,
            });
        }
        return null;
    }, [getOVLibrary]);

    const createLineFromPoints = useCallback(
        (points: any[], material: any) => {
            const OV = getOVLibrary();
            if (OV && OV.THREE) {
                const geometry = new OV.THREE.BufferGeometry().setFromPoints(points);
                return new OV.THREE.Line(geometry, material);
            }
            return null;
        },
        [getOVLibrary]
    );

    const createMarker = useCallback(
        (intersection: any, radius: number) => {
            const OV = getOVLibrary();
            if (!OV || !OV.THREE) return null;

            const markerObject = new OV.THREE.Object3D();
            const material = createMaterial();

            if (!material) return null;

            // Create circle curve
            const circleCurve = new OV.THREE.EllipseCurve(
                0.0,
                0.0,
                radius,
                radius,
                0.0,
                2.0 * Math.PI,
                false,
                0.0
            );
            const circlePoints = circleCurve.getPoints(50);
            markerObject.add(createLineFromPoints(circlePoints, material));

            // Create cross lines
            const crossLine1 = [
                new OV.THREE.Vector3(-radius, 0.0, 0.0),
                new OV.THREE.Vector3(radius, 0.0, 0.0),
            ];
            const crossLine2 = [
                new OV.THREE.Vector3(0.0, -radius, 0.0),
                new OV.THREE.Vector3(0.0, radius, 0.0),
            ];

            markerObject.add(createLineFromPoints(crossLine1, material));
            markerObject.add(createLineFromPoints(crossLine2, material));

            // Position the marker
            const faceNormal = getFaceWorldNormal(intersection);
            markerObject.updateMatrixWorld(true);
            markerObject.position.set(0.0, 0.0, 0.0);
            markerObject.lookAt(faceNormal);
            markerObject.position.set(
                intersection.point.x,
                intersection.point.y,
                intersection.point.z
            );

            return {
                intersection,
                markerObject,
            };
        },
        [getOVLibrary, createMaterial, createLineFromPoints]
    );

    const getFaceWorldNormal = useCallback(
        (intersection: any) => {
            const OV = getOVLibrary();
            if (!OV || !OV.THREE) return new (window as any).THREE.Vector3(0, 1, 0);

            const normalMatrix = new OV.THREE.Matrix4();
            intersection.object.updateWorldMatrix(true, false);
            normalMatrix.extractRotation(intersection.object.matrixWorld);
            const faceNormal = intersection.face.normal.clone();
            faceNormal.applyMatrix4(normalMatrix);
            return faceNormal;
        },
        [getOVLibrary]
    );

    const calculateMarkerValues = useCallback(
        (aMarker: Marker, bMarker: Marker): MeasureValues => {
            const OV = getOVLibrary();
            if (!OV || !OV.THREE)
                return { pointsDistance: null, parallelFacesDistance: null, facesAngle: null };

            const aIntersection = aMarker.intersection;
            const bIntersection = bMarker.intersection;

            const aNormal = getFaceWorldNormal(aIntersection);
            const bNormal = getFaceWorldNormal(bIntersection);

            const pointsDistance = aIntersection.point.distanceTo(bIntersection.point);
            const facesAngle = aNormal.angleTo(bNormal);

            let parallelFacesDistance = null;
            const BigEps = 0.0001;
            const isParallel =
                Math.abs(facesAngle) < BigEps || Math.abs(facesAngle - Math.PI) < BigEps;

            if (isParallel) {
                const aPlane = new OV.THREE.Plane().setFromNormalAndCoplanarPoint(
                    aNormal,
                    aIntersection.point
                );
                parallelFacesDistance = Math.abs(aPlane.distanceToPoint(bIntersection.point));
            }

            return {
                pointsDistance,
                parallelFacesDistance,
                facesAngle,
            };
        },
        [getOVLibrary, getFaceWorldNormal]
    );

    const addMarker = useCallback(
        (intersection: any) => {
            const viewer = getUnderlyingViewer();
            if (!viewer) return;

            // Get bounding sphere for marker size
            let radius = 0.1; // Default radius
            try {
                const boundingSphere = viewer.GetBoundingSphere(() => true);
                if (boundingSphere) {
                    radius = boundingSphere.radius / 20.0;
                }
            } catch (error) {
                console.warn("Could not get bounding sphere for marker size");
            }

            const marker = createMarker(intersection, radius);
            if (!marker) return;

            // Add marker to viewer
            try {
                viewer.AddExtraObject(marker.markerObject);
            } catch (error) {
                console.warn("Could not add marker to viewer");
            }

            setMarkers((prevMarkers) => {
                const newMarkers = [...prevMarkers, marker];

                // If we have 2 markers, add connection line
                if (newMarkers.length === 2) {
                    const material = createMaterial();
                    if (material) {
                        const aPoint = newMarkers[0].intersection.point;
                        const bPoint = newMarkers[1].intersection.point;
                        const connectionLine = createLineFromPoints([aPoint, bPoint], material);
                        if (connectionLine) {
                            try {
                                viewer.AddExtraObject(connectionLine);
                            } catch (error) {
                                console.warn("Could not add connection line to viewer");
                            }
                        }
                    }

                    // Calculate measurements
                    const values = calculateMarkerValues(newMarkers[0], newMarkers[1]);
                    setMeasureValues(values);
                }

                return newMarkers;
            });
        },
        [
            getUnderlyingViewer,
            createMarker,
            createMaterial,
            createLineFromPoints,
            calculateMarkerValues,
        ]
    );

    const clearMarkers = useCallback(() => {
        const viewer = getUnderlyingViewer();
        if (viewer) {
            try {
                viewer.ClearExtra();
            } catch (error) {
                console.warn("Could not clear extra objects from viewer");
            }
        }
        setMarkers([]);
        setTempMarker(null);
        setMeasureValues(null);
    }, [getUnderlyingViewer]);

    const handleClick = useCallback(
        (event: MouseEvent) => {
            if (!isActive) return;

            const viewer = getUnderlyingViewer();
            if (!viewer) return;

            // Get mouse coordinates relative to canvas
            const canvas = viewer.GetCanvas ? viewer.GetCanvas() : null;
            if (!canvas) {
                console.warn("Could not get canvas for measure tool");
                return;
            }

            const rect = canvas.getBoundingClientRect();
            const mouseCoordinates = {
                x: event.clientX - rect.left,
                y: event.clientY - rect.top,
            };

            console.log("Measure tool click at:", mouseCoordinates);

            try {
                // Get intersection - try different methods
                let intersection = null;

                // Try the most common method first
                if (viewer.GetMeshIntersectionUnderMouse) {
                    try {
                        intersection = viewer.GetMeshIntersectionUnderMouse(1, mouseCoordinates); // 1 = MeshOnly
                    } catch (error) {
                        console.warn("GetMeshIntersectionUnderMouse failed:", error);
                    }
                }

                // Try alternative method
                if (!intersection && viewer.GetIntersectionUnderMouse) {
                    try {
                        intersection = viewer.GetIntersectionUnderMouse(mouseCoordinates);
                    } catch (error) {
                        console.warn("GetIntersectionUnderMouse failed:", error);
                    }
                }

                if (!intersection) {
                    console.warn("No intersection found at mouse position");
                    return;
                }

                console.log("Found intersection:", intersection);

                // Clear markers if we have 2 already
                if (markers.length === 2) {
                    clearMarkers();
                }

                addMarker(intersection);
            } catch (error) {
                console.error("Error handling measure tool click:", error);
            }
        },
        [isActive, getUnderlyingViewer, markers.length, clearMarkers, addMarker]
    );

    const handleMouseMove = useCallback(
        (event: MouseEvent) => {
            if (!isActive) return;

            const viewer = getUnderlyingViewer();
            if (!viewer) return;

            const canvas = viewer.GetCanvas ? viewer.GetCanvas() : null;
            if (!canvas) return;

            const rect = canvas.getBoundingClientRect();
            const mouseCoordinates = {
                x: event.clientX - rect.left,
                y: event.clientY - rect.top,
            };

            try {
                let intersection = null;
                if (viewer.GetMeshIntersectionUnderMouse) {
                    intersection = viewer.GetMeshIntersectionUnderMouse(1, mouseCoordinates);
                }

                if (!intersection) {
                    if (tempMarker) {
                        tempMarker.markerObject.visible = false;
                        viewer.Render();
                    }
                    return;
                }

                if (!tempMarker) {
                    let radius = 0.1;
                    try {
                        const boundingSphere = viewer.GetBoundingSphere(() => true);
                        if (boundingSphere) {
                            radius = boundingSphere.radius / 20.0;
                        }
                    } catch (error) {
                        console.warn("Could not get bounding sphere for temp marker size");
                    }

                    const marker = createMarker(intersection, radius);
                    if (marker) {
                        try {
                            viewer.AddExtraObject(marker.markerObject);
                            setTempMarker(marker);
                        } catch (error) {
                            console.warn("Could not add temp marker to viewer");
                        }
                    }
                } else {
                    // Update temp marker position
                    const faceNormal = getFaceWorldNormal(intersection);
                    tempMarker.markerObject.position.set(0.0, 0.0, 0.0);
                    tempMarker.markerObject.lookAt(faceNormal);
                    tempMarker.markerObject.position.set(
                        intersection.point.x,
                        intersection.point.y,
                        intersection.point.z
                    );
                    tempMarker.markerObject.visible = true;
                    tempMarker.intersection = intersection;
                }

                viewer.Render();
            } catch (error) {
                console.warn("Error handling measure tool mouse move:", error);
            }
        },
        [isActive, getUnderlyingViewer, tempMarker, createMarker, getFaceWorldNormal]
    );

    // Set up event listeners
    useEffect(() => {
        if (isActive) {
            const viewer = getUnderlyingViewer();
            const canvas = viewer?.GetCanvas ? viewer.GetCanvas() : null;

            if (canvas) {
                mouseHandlerRef.current = handleClick;
                mouseMoveHandlerRef.current = handleMouseMove;

                canvas.addEventListener("click", handleClick);
                canvas.addEventListener("mousemove", handleMouseMove);

                return () => {
                    canvas.removeEventListener("click", handleClick);
                    canvas.removeEventListener("mousemove", handleMouseMove);
                };
            }
        } else {
            clearMarkers();
        }
    }, [isActive, handleClick, handleMouseMove, clearMarkers, getUnderlyingViewer]);

    // Position panel
    useEffect(() => {
        if (isActive && panelRef.current) {
            const viewer = getUnderlyingViewer();
            const canvas = viewer?.GetCanvas ? viewer.GetCanvas() : null;

            if (canvas) {
                const canvasRect = canvas.getBoundingClientRect();
                const panelRect = panelRef.current.getBoundingClientRect();
                const canvasWidth = canvasRect.right - canvasRect.left;
                const panelWidth = panelRect.right - panelRect.left;

                panelRef.current.style.left =
                    canvasRect.left + (canvasWidth - panelWidth) / 2 + "px";
                panelRef.current.style.top = canvasRect.top + 10 + "px";
            }
        }
    }, [isActive, measureValues, getUnderlyingViewer]);

    if (!isActive) return null;

    const getPanelContent = () => {
        if (markers.length === 0) {
            return "Select a point.";
        } else if (markers.length === 1) {
            return "Select another point.";
        } else if (measureValues) {
            return (
                <div className="ov-measure-values">
                    {measureValues.pointsDistance !== null && (
                        <div className="ov-measure-value">
                            <span className="ov-measure-icon">📏</span>
                            <span>Distance: {measureValues.pointsDistance.toFixed(3)}</span>
                        </div>
                    )}
                    {measureValues.parallelFacesDistance !== null && (
                        <div className="ov-measure-value">
                            <span className="ov-measure-icon">📐</span>
                            <span>
                                Parallel Distance: {measureValues.parallelFacesDistance.toFixed(3)}
                            </span>
                        </div>
                    )}
                    {measureValues.facesAngle !== null && (
                        <div className="ov-measure-value">
                            <span className="ov-measure-icon">∠</span>
                            <span>
                                Angle: {((measureValues.facesAngle * 180) / Math.PI).toFixed(1)}°
                            </span>
                        </div>
                    )}
                </div>
            );
        }
        return "";
    };

    const getPanelStyle = () => {
        const baseStyle: React.CSSProperties = {
            position: "fixed",
            zIndex: 1000,
            padding: "10px",
            borderRadius: "4px",
            fontSize: "14px",
            pointerEvents: "none",
            maxWidth: "300px",
        };

        if (settings.backgroundIsEnvMap) {
            return {
                ...baseStyle,
                color: "#ffffff",
                backgroundColor: "rgba(0,0,0,0.5)",
            };
        } else {
            // Determine text color based on background
            const bg = settings.backgroundColor;
            const brightness = (bg.r * 299 + bg.g * 587 + bg.b * 114) / 1000;
            const isDark = brightness < 128;

            return {
                ...baseStyle,
                color: isDark ? "#ffffff" : "#000000",
                backgroundColor: "transparent",
            };
        }
    };

    return (
        <div ref={panelRef} className="ov-measure-panel" style={getPanelStyle()}>
            {getPanelContent()}
        </div>
    );
};
