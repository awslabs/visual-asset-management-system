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
import { shownRasterKey } from "./SearchRasterLayers.keys";

/** A file hit the user asked to see as a raster. */
export interface SearchRasterRequest {
    /** Increases with every click, so asking again for a removed raster adds it again. */
    seq: number;
    /** "show" draws the raster; "hide" removes the layer that shows this file. */
    action: "show" | "hide";
    file: RasterFileRef;
    sizeBytes?: number;
}

/** The Search map's own footprint layers; rasters are kept below them so results stay clickable. */
const FOOTPRINT_LAYER_PREFIXES = ["geojson-fill-", "geojson-outline-"];

interface Props {
    map: MapLike | null;
    requests: SearchRasterRequest[];
    /** Called with the files whose raster is on the map (a converted COG counts for its source). */
    onShownChange?: (shown: Set<string>) => void;
    /**
     * The Search map's "Show raster files" filter is off: every drawn raster is taken off the map, and
     * the ones this filter hid come back with their opacity and order when it turns on again. A raster
     * the filter hid is hidden, not removed.
     */
    hidden?: boolean;
}

/**
 * Raster layers on the Search map, added from a file hit's "Show raster" action. The same layer code
 * as the GeoTIFF Map Viewer draws them (common/cogRaster); this component is loaded only when the
 * first raster is requested, so the COG reader is not part of the Search page bundle.
 */
const SearchRasterLayers: React.FC<Props> = ({ map, requests, onShownChange, hidden = false }) => {
    const layers = useRasterLayers(map, {
        beforeLayerId: () =>
            map ? firstLayerIdWithPrefix(map, FOOTPRINT_LAYER_PREFIXES) : undefined,
    });
    const { addFile, remove, setVisible, entries } = layers;
    const entriesRef = useRef(entries);
    entriesRef.current = entries;
    const handled = useRef(0);

    // The hit's footprint is already framed on the map, so an added raster is not zoomed to; the
    // layer list's "Zoom to layer" does that on request.
    useEffect(() => {
        if (!map) return;
        for (const request of requests) {
            if (request.seq <= handled.current) continue;
            handled.current = request.seq;
            if (request.action === "hide") {
                const wanted = shownRasterKey(request.file);
                for (const entry of entriesRef.current) {
                    if (shownRasterKey({ ...entry.file, key: entry.sourceKey }) === wanted) {
                        remove(entry.id);
                    }
                }
            } else {
                void addFile(request.file, { sizeBytes: request.sizeBytes });
            }
        }
    }, [map, requests, addFile, remove]);

    // Hide the drawn rasters while the filter is off, and bring back exactly those on turning it on.
    const hiddenByFilter = useRef<string[]>([]);
    useEffect(() => {
        if (hidden) {
            for (const entry of entriesRef.current) {
                if (entry.visible && !entry.failed && !hiddenByFilter.current.includes(entry.id)) {
                    hiddenByFilter.current.push(entry.id);
                    void setVisible(entry.id, false);
                }
            }
        } else if (hiddenByFilter.current.length > 0) {
            const ids = hiddenByFilter.current;
            hiddenByFilter.current = [];
            for (const id of ids) void setVisible(id, true);
        }
    }, [hidden, entries, setVisible]);

    // Report which hits are shown, so the popup's button reads Show or Hide to match (a layer removed
    // from the list below the map turns its button back to Show).
    useEffect(() => {
        onShownChange?.(
            new Set(
                entries
                    .filter((entry) => !entry.failed)
                    .map((entry) => shownRasterKey({ ...entry.file, key: entry.sourceKey }))
            )
        );
    }, [entries, onShownChange]);

    if (layers.entries.length === 0 && layers.notices.length === 0 && layers.busy === 0) {
        return null;
    }
    return (
        <div
            hidden={hidden}
            data-testid="search-raster-layers"
            data-filter-hidden={hidden || undefined}
        >
            <Container header={<Header variant="h3">Raster layers</Header>}>
                <RasterLayerPanel layers={layers} />
            </Container>
        </div>
    );
};

export default SearchRasterLayers;
