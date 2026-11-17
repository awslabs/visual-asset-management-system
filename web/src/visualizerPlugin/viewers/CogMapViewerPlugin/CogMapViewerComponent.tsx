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
            const id = `${file.databaseId}/${file.assetId}/${file.key}`;
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
        <div
            style={{ display: "flex", height: "100%", minHeight: "480px", gap: "12px" }}
            data-testid="cog-map-viewer"
        >
            <div style={{ flex: "1 1 auto", minWidth: 0, position: "relative" }}>
                <Map
                    ref={mapRef}
                    initialViewState={{ latitude: 0, longitude: 0, zoom: 1 }}
                    style={{ width: "100%", height: "100%", minHeight: "480px" }}
                    mapStyle={mapStyleUrl}
                    validateStyle={false}
                    onLoad={onLoad}
                >
                    <NavigationControl position="bottom-right" showZoom showCompass={false} />
                </Map>
            </div>
            <div style={{ flex: "0 0 320px", overflowY: "auto", paddingRight: "4px" }}>
                <SpaceBetween size="s">
                    <Box variant="h3">Raster layers</Box>
                    <RasterLayerPanel layers={layers} emptyText="No raster is on the map." />
                </SpaceBetween>
            </div>
        </div>
    );
};

export default CogMapViewerComponent;
