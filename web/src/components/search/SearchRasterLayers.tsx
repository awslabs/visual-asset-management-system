/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import { MapLike, firstLayerIdWithPrefix } from "../../common/cogRaster/RasterLayerManager";
import { RasterFileRef } from "../../common/cogRaster/presignedUrl";
import { useRasterLayers } from "../../common/cogRaster/useRasterLayers";
import RasterLayerPanel from "../../common/cogRaster/RasterLayerPanel";

/** A file hit the user asked to see as a raster. */
export interface SearchRasterRequest {
    /** Increases with every click, so asking again for a removed raster adds it again. */
    seq: number;
    file: RasterFileRef;
    sizeBytes?: number;
}

/** The Search map's own footprint layers; rasters are kept below them so results stay clickable. */
const FOOTPRINT_LAYER_PREFIXES = ["geojson-fill-", "geojson-outline-"];

interface Props {
    map: MapLike | null;
    requests: SearchRasterRequest[];
}

/**
 * Raster layers on the Search map, added from a file hit's "Show raster" action. The same layer code
 * as the GeoTIFF Map Viewer draws them (common/cogRaster); this component is loaded only when the
 * first raster is requested, so the COG reader is not part of the Search page bundle.
 */
const SearchRasterLayers: React.FC<Props> = ({ map, requests }) => {
    const layers = useRasterLayers(map, {
        beforeLayerId: () =>
            map ? firstLayerIdWithPrefix(map, FOOTPRINT_LAYER_PREFIXES) : undefined,
    });
    const { addFile } = layers;
    const handled = useRef(0);

    // The hit's footprint is already framed on the map, so an added raster is not zoomed to; the
    // layer list's "Zoom to layer" does that on request.
    useEffect(() => {
        if (!map) return;
        for (const request of requests) {
            if (request.seq <= handled.current) continue;
            handled.current = request.seq;
            void addFile(request.file, { sizeBytes: request.sizeBytes });
        }
    }, [map, requests, addFile]);

    if (layers.entries.length === 0 && layers.notices.length === 0 && layers.busy === 0) {
        return null;
    }
    return (
        <Container header={<Header variant="h3">Raster layers</Header>}>
            <RasterLayerPanel layers={layers} />
        </Container>
    );
};

export default SearchRasterLayers;
