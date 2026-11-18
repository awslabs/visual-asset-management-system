/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Map, { MapRef, NavigationControl } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import "../../../common/utils/maplibreWorker";
import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { appCache } from "../../../services/appCache";
import { ViewerPluginProps } from "../../core/types";
import { RasterFileRef } from "../../../common/cogRaster/presignedUrl";
import { isTiffName } from "../../../common/cogRaster/rasterResolver";
import { useRasterLayers } from "../../../common/cogRaster/useRasterLayers";
import RasterLayerPanel from "../../../common/cogRaster/RasterLayerPanel";

interface SizedFile extends RasterFileRef {
    sizeBytes?: number;
}

/** The files the viewer was asked to show, each under its own asset (a selection may span assets). */
export function filesToShow(props: ViewerPluginProps): SizedFile[] {
    const { assetId, databaseId, assetKey, multiFileKeys, multiFiles, versionId, assetVersionId } =
        props;
    if (multiFiles && multiFiles.length > 0) {
        return multiFiles
            .filter((file) => !file.isDirectory && isTiffName(file.key))
            .map((file) => ({
                databaseId: file.databaseId || databaseId,
                assetId: file.assetId || assetId,
                key: file.key,
                versionId: file.versionId,
                sizeBytes: file.size,
            }));
    }
    const keys =
        multiFileKeys && multiFileKeys.length > 0 ? multiFileKeys : assetKey ? [assetKey] : [];
    return keys
        .filter((key) => isTiffName(key))
        .map((key) => ({
            databaseId,
            assetId,
            key,
            versionId: keys.length === 1 ? versionId : undefined,
            assetVersionId: keys.length === 1 ? (assetVersionId as string | undefined) : undefined,
        }));
}

const NO_ANCHOR = () => undefined;

/**
 * Layout: the viewer fills the visualizer frame it is placed in (the frame is the page's or the modal's
 * own size, and fullscreen makes it the screen), instead of choosing a height of its own. A fixed height
 * left the map clipped below a shorter frame, with its controls outside the frame, and showed the
 * frame's striped backdrop under the map and the layer list in a taller one. The viewer paints an opaque
 * background over the whole frame, so the backdrop never shows through behind the layer list or the map
 * controls.
 *
 * The map and the layer list are two wrapping flex items. The map's basis is large and its grow factor
 * much larger than the list's, so on a wide viewer the list sits beside the map and takes its 300 px
 * basis, and on a phone or tablet the two no longer fit on one row, wrap, and each takes the full width,
 * the list below the map (the viewer scrolls). Both fill the frame's height when side by side; the list
 * scrolls inside itself. `min-width: 0` stops long content from pushing the map narrower. The DOM order
 * (map, then list) is the focus order at every width.
 */
export const MAP_MIN_HEIGHT_PX = 360;
const VIEWER_BACKGROUND = "var(--vams-bg-primary, #0f1b2a)";
const VIEWER_STYLE: React.CSSProperties = {
    display: "flex",
    flexWrap: "wrap",
    alignContent: "stretch",
    gap: "12px",
    width: "100%",
    height: "100%",
    minWidth: 0,
    minHeight: `${MAP_MIN_HEIGHT_PX}px`,
    overflowX: "hidden",
    overflowY: "auto",
    boxSizing: "border-box",
    background: VIEWER_BACKGROUND,
};
const MAP_COLUMN_STYLE: React.CSSProperties = {
    flex: "999 1 480px",
    minWidth: 0,
    position: "relative",
    height: "100%",
    minHeight: `${MAP_MIN_HEIGHT_PX}px`,
    boxSizing: "border-box",
};
/** The width below which the map (480 px basis) and the list (300 px basis, 12 px gap) no longer fit on one row. */
export const STACKED_BELOW_PX = 480 + 300 + 12;
/** Share of the frame the map takes when the list sits under it, so the top of the list stays in view. */
const STACKED_MAP_HEIGHT = "65%";
const LIST_COLUMN_STYLE: React.CSSProperties = {
    flex: "1 1 300px",
    minWidth: 0,
    maxHeight: "100%",
    overflowX: "hidden",
    overflowY: "auto",
    padding: "0 8px 8px 4px",
    boxSizing: "border-box",
    background: VIEWER_BACKGROUND,
};

/**
 * Shows georeferenced TIFFs on the Location Service basemap. A Web Mercator COG streams as map tiles
 * (the COG tile protocol over a presigned URL, with ranged reads); a small file in another bundled
 * coordinate system is drawn as one placed image and labelled as an approximate placement. Layer
 * list, opacity, order, zoom, refresh and cleanup come from the same code the Search map uses
 * (common/cogRaster). Whether a file may be read is decided by the API on every request.
 */
const CogMapViewerComponent: React.FC<ViewerPluginProps> = (props) => {
    const config = appCache.getItem("config");
    const mapStyleUrl: string | undefined = config?.locationServiceApiUrl;
    const mapRef = useRef<MapRef>(null);
    const [map, setMap] = useState<any>(null);
    const layers = useRasterLayers(map, { beforeLayerId: NO_ANCHOR });
    const { addFile } = layers;

    // The props hold fresh arrays on every render; the viewer reacts to the files they name.
    const filesKey = JSON.stringify([
        props.assetId,
        props.databaseId,
        props.assetKey,
        props.versionId,
        props.assetVersionId,
        props.multiFileKeys ?? [],
        (props.multiFiles ?? []).map((f) => [f.databaseId, f.assetId, f.key, f.versionId, f.size]),
    ]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    const files = useMemo(() => filesToShow(props), [filesKey]);

    // Files the user removed from the list stay removed until the selection itself changes.
    const requested = useRef<{ key: string; ids: Set<string> }>({ key: "", ids: new Set() });
    useEffect(() => {
        if (!map) return;
        if (requested.current.key !== filesKey)
            requested.current = { key: filesKey, ids: new Set() };
        for (const file of files) {
            const id = `${file.databaseId}/${file.assetId}/${file.key}@${file.versionId ?? ""}`;
            if (requested.current.ids.has(id)) continue;
            requested.current.ids.add(id);
            void addFile(file, { sizeBytes: file.sizeBytes });
        }
    }, [map, files, filesKey, addFile]);

    const onLoad = useCallback(() => {
        setMap(mapRef.current?.getMap() ?? null);
    }, []);

    // Frame the first raster that can be framed. A tile layer's extent can arrive after the layer is
    // listed (from its source once maplibre has read it), so framing waits until zoomTo succeeds.
    const framed = useRef(false);
    const { entries, zoomTo } = layers;
    useEffect(() => {
        if (framed.current) return;
        const first = entries.find((entry) => entry.framable && !entry.failed);
        if (first && zoomTo(first.id)) framed.current = true;
    }, [entries, zoomTo]);

    // Whether the list has wrapped under the map: then the map leaves room for the top of the list.
    const rootRef = useRef<HTMLDivElement>(null);
    const [stacked, setStacked] = useState(false);
    const hasFiles = files.length > 0;
    const hasViewerRoot = !!appCache.getItem("config")?.locationServiceApiUrl;
    useEffect(() => {
        const root = rootRef.current;
        if (!root || typeof ResizeObserver === "undefined") return undefined;
        const observer = new ResizeObserver(([entry]) =>
            setStacked(entry.contentRect.width < STACKED_BELOW_PX)
        );
        observer.observe(root);
        return () => observer.disconnect();
    }, [hasViewerRoot, hasFiles]);

    if (!mapStyleUrl) {
        return (
            <Box padding="l" textAlign="center" color="text-status-inactive">
                The map is not available: location services are not configured for this environment.
            </Box>
        );
    }

    if (files.length === 0) {
        return (
            <Box padding="l" textAlign="center" color="text-status-inactive">
                Select a .tif or .tiff file to show on the map.
            </Box>
        );
    }

    return (
        <div ref={rootRef} style={VIEWER_STYLE} data-testid="cog-map-viewer">
            <div
                style={
                    stacked ? { ...MAP_COLUMN_STYLE, height: STACKED_MAP_HEIGHT } : MAP_COLUMN_STYLE
                }
                data-testid="cog-map-column"
            >
                <Map
                    ref={mapRef}
                    initialViewState={{ latitude: 0, longitude: 0, zoom: 1 }}
                    style={{ width: "100%", height: "100%" }}
                    mapStyle={mapStyleUrl}
                    validateStyle={false}
                    onLoad={onLoad}
                >
                    <NavigationControl position="bottom-right" showZoom showCompass={false} />
                </Map>
            </div>
            <div style={LIST_COLUMN_STYLE} data-testid="cog-layer-column">
                <SpaceBetween size="s">
                    <Box variant="h3">Raster layers</Box>
                    <RasterLayerPanel layers={layers} emptyText="No raster is on the map." />
                </SpaceBetween>
            </div>
        </div>
    );
};

export default CogMapViewerComponent;
