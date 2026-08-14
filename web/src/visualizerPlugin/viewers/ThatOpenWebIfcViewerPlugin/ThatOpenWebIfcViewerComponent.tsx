/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { appCache } from "../../../services/appCache";
import { ViewerPluginProps } from "../../core/types";
import { getDualAuthorizationHeader } from "../../../utils/authTokenUtils";
import LoadingSpinner from "../../components/LoadingSpinner";
import { ThatOpenWebIfcDependencyManager } from "./dependencies";
import ThatOpenWebIfcPanel from "./ThatOpenWebIfcPanel";
import { IfcViewerInstance, SelectedElement, SpatialNode } from "./types";
import { extractIfcBytes, loadIfcModel, fitCameraToModels } from "./utils/ifcLoader";
import { buildSpatialTree } from "./utils/spatialTree";

// Self-hosted Fragments model worker, copied next to the bundle by the
// customInstall (see customInstalls/thatopenwebifc). Served same-origin so it
// works in air-gapped/GovCloud and inside the COEP isolation boundary.
const FRAGMENTS_WORKER_URL = "/viewers/thatopenwebifc/thatopenwebifc-fragments-worker.js";

const ThatOpenWebIfcViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
    assetVersionId,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const [config] = useState(appCache.getItem("config"));
    const [bundleReady, setBundleReady] = useState(false);
    const [isLoading, setIsLoading] = useState(true);
    const [loadingMessage, setLoadingMessage] = useState("Initializing viewer...");
    const [error, setError] = useState<string | null>(null);
    const [sceneReady, setSceneReady] = useState(false);
    const [showPanel, setShowPanel] = useState(true);

    const viewerInstanceRef = useRef<IfcViewerInstance | null>(null);
    const initializationRef = useRef(false);
    const loadingCancelledRef = useRef(false);
    // Aborted on unmount so closing the viewer mid-download stops the transfer instead of letting it
    // run to completion for a file nobody is looking at.
    const downloadAbortRef = useRef<AbortController | null>(null);

    // Model tree + selection state surfaced to the panel.
    const [spatialTree, setSpatialTree] = useState<SpatialNode | null>(null);
    const [selectedElement, setSelectedElement] = useState<SelectedElement | null>(null);
    const [elementCount, setElementCount] = useState(0);
    // Currently-selected local IDs, used to sync the Model Tree highlight with
    // the 3D selection (both directions). Updated by clicks in the 3D view and
    // by clicks in the tree.
    const [selectedLocalIds, setSelectedLocalIds] = useState<number[]>([]);
    // Guards against feedback loops: when WE drive the highlighter from a tree
    // click, the resulting onHighlight event should not re-trigger a tree-driven
    // update. Stored in a ref so the event handler always sees the latest value.
    const suppressHighlightEventRef = useRef(false);

    // ---- Step 1: load the That Open bundle (window.ThatOpenWebIfcBundle) ----
    useEffect(() => {
        const initialize = async () => {
            try {
                setLoadingMessage("Loading IFC/BIM viewer dependencies...");
                await ThatOpenWebIfcDependencyManager.loadThatOpenWebIfc();
                setBundleReady(true);
            } catch (err) {
                console.error("Failed to initialize That Open Engine:", err);
                setError("Failed to load IFC/BIM viewer dependencies");
                setIsLoading(false);
            }
        };
        if (!bundleReady) initialize();
    }, [bundleReady]);

    // ---- Step 2: build world, fetch + load the IFC, wire selection ----
    useEffect(() => {
        if (
            !assetKey ||
            initializationRef.current ||
            !bundleReady ||
            !config ||
            !containerRef.current
        ) {
            return;
        }
        initializationRef.current = true;

        const handleResize = () => {
            const inst = viewerInstanceRef.current;
            if (inst?.world?.renderer?.resize) {
                inst.world.renderer.resize();
            }
        };

        const loadAsset = async () => {
            try {
                const bundle = ThatOpenWebIfcDependencyManager.getBundle();
                const { OBC, OBF } = bundle;

                setLoadingMessage("Initializing 3D scene...");

                // The enclosing effect guards `containerRef.current` is non-null
                // before invoking loadAsset; capture it in a local for clarity.
                const container = containerRef.current;
                if (!container) return;

                // Build Components + a World (scene / camera / renderer).
                // Use OBF.RendererWith2D (a SimpleRenderer subclass that also
                // hosts a CSS2DRenderer) instead of OBC.SimpleRenderer so that
                // measurement labels — the length/area numbers, which are
                // CSS2DObjects — actually render over the canvas. SimpleRenderer
                // has no 2D label layer, which is why the values were invisible.
                const components = new OBC.Components();
                const worlds = components.get(OBC.Worlds);
                const world = worlds.create();
                world.scene = new OBC.SimpleScene(components);
                world.renderer = new OBF.RendererWith2D(components, container);
                world.camera = new OBC.SimpleCamera(components);
                components.init();
                world.scene.setup();
                world.scene.three.background = new bundle.THREE.Color(0x333333);

                // Grid.
                components.get(OBC.Grids).create(world);

                // Fragments runtime worker (self-hosted, same-origin). Fall back to
                // the library's bundled getWorker() if the self-hosted file is
                // unreachable for any reason.
                const fragments = components.get(OBC.FragmentsManager);
                try {
                    fragments.init(FRAGMENTS_WORKER_URL);
                } catch (workerErr) {
                    console.warn(
                        "ThatOpenWebIfc: self-hosted worker init failed, trying getWorker():",
                        workerErr
                    );
                    const fallback = await OBC.FragmentsManager.getWorker();
                    fragments.init(fallback);
                }

                // Keep the fragments core in sync with the camera, and auto-add
                // loaded models to the scene.
                world.camera.controls.addEventListener("update", () => fragments.core.update());
                fragments.list.onItemSet.add(async ({ value: model }: any) => {
                    model.useCamera(world.camera.three);
                    world.scene.three.add(model.object);
                    await fragments.core.update(true);
                });

                if (loadingCancelledRef.current) return;

                // Fetch the file bytes from the streaming endpoint (same flow as
                // the Three.js viewer): dual-auth header + version query params.
                setLoadingMessage(`Downloading ${assetKey.split("/").pop()}...`);
                const authHeader = await getDualAuthorizationHeader();
                const encodedFileKey = assetKey
                    .split("/")
                    .map((seg) => encodeURIComponent(seg))
                    .join("/");
                let assetUrl = `${config.api}database/${databaseId}/assets/${assetId}/download/stream/${encodedFileKey}`;
                if (assetVersionId) {
                    assetUrl += `?assetVersionId=${encodeURIComponent(assetVersionId)}`;
                } else if (versionId) {
                    assetUrl += `?versionId=${encodeURIComponent(versionId)}`;
                }
                downloadAbortRef.current = new AbortController();
                const response = await fetch(assetUrl, {
                    headers: { Authorization: authHeader },
                    signal: downloadAbortRef.current.signal,
                });
                if (!response.ok) {
                    throw new Error(
                        `Failed to load: ${assetKey.split("/").pop()} (${response.status})`
                    );
                }
                const arrayBuffer = await response.arrayBuffer();
                if (loadingCancelledRef.current) return;

                // Extract raw IFC bytes (.ifc passthrough, .ifczip unzip).
                const fileName = assetKey.split("/").pop() || "model.ifc";
                setLoadingMessage("Parsing IFC model...");
                const ifcBytes = extractIfcBytes(bundle, arrayBuffer, fileName);

                // Load the model via the WASM IfcLoader.
                const model = await loadIfcModel(
                    bundle,
                    components,
                    ifcBytes,
                    `${assetId}:${fileName}`,
                    (percent) => setLoadingMessage(`Parsing IFC model... ${percent}%`)
                );
                if (loadingCancelledRef.current) return;
                await fragments.core.update(true);

                // Detect schema for the stats overlay (best-effort).
                let schema = "IFC";
                try {
                    schema = model?.schema || model?.ifcMetadata?.schema || "IFC";
                } catch {
                    /* schema is best-effort */
                }

                // Selection -> properties (Highlighter + Raycaster). Created
                // before the instance is stored so the panel/tree can drive it.
                let highlighter: any = null;
                try {
                    components.get(OBC.Raycasters).get(world);
                    highlighter = components.get(OBF.Highlighter);
                    highlighter.setup({ world });
                    // Fires on 3D click selection. Mirror the selection into the
                    // Properties tab AND the Model Tree highlight. Skip the tree
                    // sync when WE triggered the highlight from a tree click
                    // (suppressHighlightEventRef) to avoid a feedback loop.
                    highlighter.events.select.onHighlight.add(async (modelIdMap: any) => {
                        const allIds: number[] = [];
                        let firstData: any = null;
                        for (const [mId, localIds] of Object.entries(modelIdMap)) {
                            const m = fragments.list.get(mId);
                            if (!m) continue;
                            const ids = [...(localIds as Set<number>)];
                            if (ids.length === 0) continue;
                            allIds.push(...ids);
                            if (firstData === null) {
                                const data = await m.getItemsData([ids[0]]);
                                firstData = { localId: ids[0], data: data?.[0] };
                            }
                        }
                        if (firstData) {
                            setSelectedElement(
                                normalizeSelection(firstData.localId, firstData.data)
                            );
                        }
                        if (!suppressHighlightEventRef.current) {
                            setSelectedLocalIds(allIds);
                        }
                    });
                    highlighter.events.select.onClear.add(() => {
                        setSelectedElement(null);
                        if (!suppressHighlightEventRef.current) {
                            setSelectedLocalIds([]);
                        }
                    });
                } catch (selErr) {
                    console.warn("ThatOpenWebIfc: failed to wire selection:", selErr);
                }

                viewerInstanceRef.current = {
                    components,
                    world,
                    fragments,
                    model,
                    highlighter,
                    schema,
                };

                // Fit camera.
                setLoadingMessage("Positioning camera...");
                await fitCameraToModels(bundle, components, world);

                // Build the model tree.
                try {
                    const tree = await buildSpatialTree(bundle, components, model);
                    setSpatialTree(tree);
                    const count = tree.children.reduce(
                        (sum, group) => sum + group.children.length,
                        0
                    );
                    setElementCount(count);
                } catch (treeErr) {
                    console.warn("ThatOpenWebIfc: failed to build spatial tree:", treeErr);
                }

                setSceneReady(true);
                setIsLoading(false);

                // Keep renderer sized to the container.
                window.addEventListener("resize", handleResize);
            } catch (err) {
                console.error("Error loading IFC asset:", err);
                setError(err instanceof Error ? err.message : "Failed to load IFC file");
                setIsLoading(false);
            }
        };

        loadAsset();

        // ---- Cleanup on unmount ----
        return () => {
            console.log("ThatOpenWebIfc Viewer: Cleanup initiated");
            loadingCancelledRef.current = true;
            downloadAbortRef.current?.abort();
            downloadAbortRef.current = null;
            window.removeEventListener("resize", handleResize);
            try {
                viewerInstanceRef.current?.components?.dispose();
            } catch (disposeErr) {
                console.warn("ThatOpenWebIfc: dispose error:", disposeErr);
            }
            viewerInstanceRef.current = null;
            ThatOpenWebIfcDependencyManager.cleanup();
            console.log("ThatOpenWebIfc Viewer: Cleanup complete");
        };
    }, [bundleReady, assetKey, assetId, databaseId, versionId, assetVersionId, config]);

    // Escape hides the control panel (matches the Three.js viewer's shortcut).
    // Ignore keypresses that originate from text inputs so typing isn't hijacked.
    useEffect(() => {
        const handleKeyPress = (event: KeyboardEvent) => {
            if (
                event.target instanceof HTMLInputElement ||
                event.target instanceof HTMLTextAreaElement
            ) {
                return;
            }
            if (event.key === "Escape" && showPanel) {
                setShowPanel(false);
            }
        };
        window.addEventListener("keydown", handleKeyPress);
        return () => window.removeEventListener("keydown", handleKeyPress);
    }, [showPanel]);

    // Tree -> 3D selection. Called when the user clicks a node in the Model Tree.
    // Drives the Highlighter to color the matching elements in the 3D view and
    // populates the Properties tab. The suppress flag prevents the resulting
    // highlighter event from clobbering the tree selection we just set.
    const selectByLocalIds = useCallback(async (localIds: number[]) => {
        const inst = viewerInstanceRef.current;
        if (!inst?.highlighter || !inst?.model) return;

        setSelectedLocalIds(localIds);

        try {
            if (localIds.length === 0) {
                suppressHighlightEventRef.current = true;
                await inst.highlighter.clear("select");
                setSelectedElement(null);
                suppressHighlightEventRef.current = false;
                return;
            }

            const modelIdMap = { [inst.model.modelId]: new Set<number>(localIds) };

            suppressHighlightEventRef.current = true;
            await inst.highlighter.highlightByID("select", modelIdMap, true, false);
            suppressHighlightEventRef.current = false;

            // Populate the Properties tab from the first selected element.
            const data = await inst.model.getItemsData([localIds[0]]);
            setSelectedElement(normalizeSelection(localIds[0], data?.[0]));
        } catch (err) {
            suppressHighlightEventRef.current = false;
            console.warn("ThatOpenWebIfc: tree selection failed:", err);
        }
    }, []);

    if (error) {
        return (
            <div
                style={{ position: "relative", height: "100%", backgroundColor: "#f5f5f5" }}
                id="thatopenwebifc-viewer-root"
            >
                <div
                    style={{
                        color: "#d13212",
                        fontSize: "1.4em",
                        lineHeight: "1.5",
                        maxWidth: "800px",
                        position: "absolute",
                        top: "50%",
                        left: "50%",
                        transform: "translate(-50%, -50%)",
                        textAlign: "center",
                        padding: "20px",
                    }}
                >
                    {error}
                    <br />
                    <br />
                    <span style={{ fontSize: ".9em", color: "#d13212" }}>
                        Please ensure the file is a supported IFC (.ifc / .ifczip) format
                    </span>
                </div>
            </div>
        );
    }

    return (
        <div
            style={{ position: "relative", height: "100%", width: "100%" }}
            id="thatopenwebifc-viewer-root"
        >
            <div
                ref={containerRef}
                style={{ width: "100%", height: "100%" }}
                id="thatopenwebifc-viewer-container"
            />

            {isLoading && <LoadingSpinner message={loadingMessage} />}

            {sceneReady && viewerInstanceRef.current && showPanel && (
                <ThatOpenWebIfcPanel
                    instance={viewerInstanceRef.current}
                    bundle={ThatOpenWebIfcDependencyManager.getBundle()}
                    spatialTree={spatialTree}
                    selectedElement={selectedElement}
                    selectedLocalIds={selectedLocalIds}
                    onSelectLocalIds={selectByLocalIds}
                    onClose={() => setShowPanel(false)}
                />
            )}

            {sceneReady && !showPanel && (
                <button
                    onClick={() => setShowPanel(true)}
                    style={{
                        position: "absolute",
                        top: "20px",
                        left: "10px",
                        backgroundColor: "rgba(0, 0, 0, 0.7)",
                        color: "white",
                        border: "1px solid rgba(255, 255, 255, 0.2)",
                        padding: "8px 12px",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "0.8em",
                        zIndex: 1000,
                    }}
                    title="Show controls panel"
                >
                    ⚙️ Panel
                </button>
            )}

            {sceneReady && viewerInstanceRef.current && (
                <div
                    style={{
                        position: "absolute",
                        top: "10px",
                        right: "10px",
                        color: "white",
                        fontSize: "12px",
                        backgroundColor: "rgba(0,0,0,0.7)",
                        padding: "8px",
                        borderRadius: "4px",
                        zIndex: 1000,
                    }}
                >
                    <div style={{ fontWeight: "bold", marginBottom: "4px" }}>
                        ThatOpen IFC BIM Viewer
                    </div>
                    <div style={{ fontSize: "0.9em", opacity: 0.9 }}>
                        Mouse: Rotate | Wheel: Zoom | Right-click: Pan
                    </div>
                    <div style={{ fontSize: "0.85em", marginTop: "6px", color: "#4CAF50" }}>
                        🏛️ Schema: {viewerInstanceRef.current.schema}
                    </div>
                    {elementCount > 0 && (
                        <div style={{ fontSize: "0.85em", marginTop: "4px", color: "#2196F3" }}>
                            📦 {elementCount.toLocaleString()} elements
                        </div>
                    )}
                    {selectedElement && (
                        <div style={{ fontSize: "0.85em", marginTop: "4px", color: "#FF9800" }}>
                            ✓ {selectedElement.category} #{selectedElement.localId}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

/**
 * Normalizes a raw getItemsData() record into the SelectedElement shape used by
 * the Properties tab. Defensive: getItemsData output shape varies by version.
 */
function normalizeSelection(localId: number, data: any): SelectedElement {
    const name = data?.Name?.value || data?.attributes?.Name?.value || data?.Name || `#${localId}`;
    const category = data?.category || data?._category?.value || data?.type || "Element";

    const propertySets: SelectedElement["propertySets"] = [];
    const psets = data?.psets || data?.propertySets || {};
    try {
        for (const psetName of Object.keys(psets)) {
            const props = psets[psetName] || {};
            const properties = Object.keys(props).map((k) => ({
                name: k,
                value: String(props[k]?.value ?? props[k] ?? ""),
            }));
            propertySets.push({ name: psetName, properties });
        }
    } catch {
        /* best-effort property extraction */
    }

    return { localId, name: String(name), category: String(category), propertySets };
}

export default ThatOpenWebIfcViewerComponent;
