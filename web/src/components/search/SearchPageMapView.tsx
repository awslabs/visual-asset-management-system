/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import Map, {
    Marker,
    Popup,
    NavigationControl,
    MapRef,
    Source,
    Layer,
} from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { SearchPageViewProps } from "./SearchPageTypes";
import {
    Box,
    Button,
    Link,
    Pagination,
    SpaceBetween,
    Popover,
    Icon,
} from "@cloudscape-design/components";
import { appCache } from "../../services/appCache";
import type { LngLatBoundsLike } from "maplibre-gl";
import PreviewThumbnailCell from "./SearchPreviewThumbnail/PreviewThumbnailCell";
import { SearchExplanation, getTotalResultCount } from "./types";
import { extractLocationData } from "./utils/locationUtils";
import { colorForKey } from "./utils/polygonColor";
import { formatFileSizeForDisplay } from "../../common/utils/fileSize";

interface LocationDataWithDetails {
    id: string;
    type: "point" | "geojson";
    rectype: "asset" | "file";
    databaseId: string;
    assetId: string;
    assetName: string;
    fileKey?: string;
    fileExt?: string;
    /** File size in bytes — file results only. */
    fileSize?: number;
    /** ISO date string the record was created — usually upload time for files. */
    dateCreated?: string;
    /** Display name of the parent asset, used in the file popup. */
    parentAssetName?: string;
    description?: string;
    tags?: string[];
    explanation?: SearchExplanation;
    metadata?: Array<{ name: string; type: string; value: any }>;
    attributes?: Array<{ name: string; type: string; value: any }>;
    // For points
    latitude?: number;
    longitude?: number;
    // For GeoJSON
    geoJson?: any;
}

// Helper component to render explanation popover
const ExplanationPopover: React.FC<{ explanation: SearchExplanation }> = ({ explanation }) => (
    <Popover
        size="large"
        position="right"
        triggerType="custom"
        dismissButton={false}
        content={
            <SpaceBetween size="s">
                <Box variant="h4">Why this result matched</Box>
                <Box>
                    <strong>Query Type:</strong> {explanation.query_type}
                </Box>
                <Box>
                    <strong>Index:</strong> {explanation.index_type}
                </Box>
                <Box>
                    <strong>Score:</strong> {explanation.score_breakdown.total_score.toFixed(2)}
                </Box>
                {explanation.matched_fields.length > 0 && (
                    <>
                        <Box variant="h5">
                            Matched Fields ({explanation.matched_fields.length}):
                        </Box>
                        <Box>
                            <ul style={{ margin: 0, paddingLeft: "20px" }}>
                                {explanation.matched_fields.slice(0, 5).map((field, idx) => (
                                    <li key={idx}>
                                        <strong>{field}:</strong>{" "}
                                        {explanation.match_reasons[field] || "Matched"}
                                    </li>
                                ))}
                                {explanation.matched_fields.length > 5 && (
                                    <li>
                                        ...and {explanation.matched_fields.length - 5} more fields
                                    </li>
                                )}
                            </ul>
                        </Box>
                    </>
                )}
            </SpaceBetween>
        }
    >
        <Icon name="status-info" variant="link" />
    </Popover>
);

// Helper component to render metadata and attributes popover
const MetadataPopover: React.FC<{
    metadata: Array<{ name: string; type: string; value: any }>;
    attributes: Array<{ name: string; type: string; value: any }>;
}> = ({ metadata, attributes }) => {
    // Don't show popover if both arrays are empty
    if (metadata.length === 0 && attributes.length === 0) {
        return null;
    }

    return (
        <Popover
            size="large"
            position="right"
            triggerType="custom"
            dismissButton={false}
            content={
                <SpaceBetween size="s">
                    {/* Metadata Fields Section - only show if there are metadata fields */}
                    {metadata.length > 0 && (
                        <>
                            <Box variant="h4">Metadata Fields ({metadata.length})</Box>
                            <Box>
                                <ul style={{ margin: 0, paddingLeft: "20px" }}>
                                    {metadata.map((field, idx) => (
                                        <li key={idx}>
                                            <strong>
                                                {field.name} ({field.type}):
                                            </strong>{" "}
                                            {String(field.value)}
                                        </li>
                                    ))}
                                </ul>
                            </Box>
                        </>
                    )}

                    {/* Attribute Fields Section - only show if there are attribute fields */}
                    {attributes.length > 0 && (
                        <>
                            <Box variant="h4">Attribute Fields ({attributes.length})</Box>
                            <Box>
                                <ul style={{ margin: 0, paddingLeft: "20px" }}>
                                    {attributes.map((field, idx) => (
                                        <li key={idx}>
                                            <strong>
                                                {field.name} ({field.type}):
                                            </strong>{" "}
                                            {String(field.value)}
                                        </li>
                                    ))}
                                </ul>
                            </Box>
                        </>
                    )}
                </SpaceBetween>
            }
        >
            <Icon name="status-info" variant="link" />
        </Popover>
    );
};

// Helper function to infer type from value
const inferType = (value: any): string => {
    if (value === null || value === undefined) {
        return "Unknown";
    }
    if (typeof value === "number") {
        return "Number";
    }
    if (typeof value === "boolean") {
        return "Boolean";
    }
    if (Array.isArray(value)) {
        return "List";
    }
    if (typeof value === "string") {
        // Check if it's a date string
        if (!isNaN(Date.parse(value)) && value.match(/^\d{4}-\d{2}-\d{2}/)) {
            return "Date";
        }
        return "String";
    }
    if (typeof value === "object") {
        return "Object";
    }
    return "Unknown";
};

// Helper function to extract and format metadata and attribute fields with type information
const extractMetadata = (
    item: any
): {
    metadata: Array<{ name: string; type: string; value: any }>;
    attributes: Array<{ name: string; type: string; value: any }>;
} => {
    const metadata: Array<{ name: string; type: string; value: any }> = [];
    const attributes: Array<{ name: string; type: string; value: any }> = [];

    // Check if MD_ exists as an object (new format)
    if (item.MD_ && typeof item.MD_ === "object" && !Array.isArray(item.MD_)) {
        Object.entries(item.MD_).forEach(([key, value]) => {
            metadata.push({
                name: key,
                type: inferType(value),
                value: value,
            });
        });
    }

    // Check if AB_ exists as an object (new format)
    if (item.AB_ && typeof item.AB_ === "object" && !Array.isArray(item.AB_)) {
        Object.entries(item.AB_).forEach(([key, value]) => {
            attributes.push({
                name: key,
                type: inferType(value),
                value: value,
            });
        });
    }

    return { metadata, attributes };
};

function SearchPageMapView({ state, dispatch }: SearchPageViewProps) {
    const [selectedItem, setSelectedItem] = useState<LocationDataWithDetails | null>(null);
    const mapRef = useRef<MapRef>(null);
    const config = appCache.getItem("config");
    const navigate = useNavigate();

    // Get pagination info from state
    const pageSize = state.tablePreferences?.pageSize || 50;
    const currentPage = 1 + Math.floor((state.pagination?.from || 0) / pageSize);
    const totalResults = getTotalResultCount(state?.result);
    const pageCount = Math.ceil(totalResults / pageSize);

    // The "Files" / "Assets" toggle on the search page is driven by the
    // `_rectype` filter on search state, not by a per-document field. Read
    // it once per render so every result picked up by this map view is
    // classified consistently with the rest of the search UI (table view,
    // list-mode buttons, etc.). Falling back to per-source `_rectype` is
    // unreliable because that field isn't always populated on hit sources.
    const isFileSearchMode = state.filters?._rectype?.value === "file";

    // Extract location data from search results
    const locationData: LocationDataWithDetails[] = React.useMemo(() => {
        if (!state.result?.hits?.hits) return [];

        const validData: LocationDataWithDetails[] = [];

        state.result.hits.hits.forEach((hit: any) => {
            const source = hit._source;
            if (!source) return;

            const location = extractLocationData(source);
            if (location && location.type) {
                const { metadata, attributes } = extractMetadata(source);
                // Authoritative source: the search-state `_rectype` filter.
                // Fall back to per-source `_rectype` only if the filter
                // is unset (e.g., a future "all" mode).
                const isFile = isFileSearchMode || source._rectype === "file";
                validData.push({
                    ...location,
                    id: hit._id,
                    rectype: isFile ? "file" : "asset",
                    databaseId: source.str_databaseid,
                    assetId: source.str_assetid,
                    // For files we want the popup heading to be the file
                    // (so the user immediately sees the path) and a
                    // separate row to show the parent asset name. For
                    // asset rows we keep the previous behavior.
                    assetName: isFile
                        ? source.str_key || "Unnamed File"
                        : source.str_assetname || "Unnamed Asset",
                    parentAssetName: isFile ? source.str_assetname : undefined,
                    fileKey: source.str_key,
                    fileExt: source.str_fileext,
                    fileSize: isFile ? source.num_filesize ?? source.num_size : undefined,
                    dateCreated: source.date_created,
                    description: source.str_description,
                    tags: source.list_tags,
                    explanation: hit.explanation,
                    metadata,
                    attributes,
                } as LocationDataWithDetails);
            }
        });

        console.log(
            `[MapView] Extracted ${validData.length} items with location data from ${state.result.hits.hits.length} results`
        );
        return validData;
    }, [state.result, isFileSearchMode]);

    // Reset selected items when location data changes (page change or new search)
    useEffect(() => {
        setSelectedItem(null);
    }, [locationData]);

    // Compute the popup anchor (lon, lat) for a GeoJSON-shaped result. Uses
    // the centroid of the bounding box of all coordinates in the geometry —
    // simple and robust across Polygon, MultiPolygon, LineString and
    // GeometryCollection without pulling in a turf-style dependency.
    const computeGeoJsonPopupAnchor = (item: LocationDataWithDetails): [number, number] | null => {
        if (item.type !== "geojson" || !item.geoJson) return null;
        const lons: number[] = [];
        const lats: number[] = [];
        const collect = (coords: any) => {
            if (!Array.isArray(coords)) return;
            if (typeof coords[0] === "number" && typeof coords[1] === "number") {
                lons.push(coords[0]);
                lats.push(coords[1]);
                return;
            }
            for (const c of coords) collect(c);
        };
        const geom = item.geoJson?.type === "Feature" ? item.geoJson.geometry : item.geoJson;
        if (geom?.coordinates) collect(geom.coordinates);
        if (geom?.geometries) {
            for (const sub of geom.geometries) {
                if (sub?.coordinates) collect(sub.coordinates);
            }
        }
        if (lons.length === 0) return null;
        const minLon = Math.min(...lons);
        const maxLon = Math.max(...lons);
        const minLat = Math.min(...lats);
        const maxLat = Math.max(...lats);
        return [(minLon + maxLon) / 2, (minLat + maxLat) / 2];
    };

    // Build the list of fill-layer IDs we want to be click-interactive so
    // MapLibre routes pointer events through to the map's onClick. The set
    // changes as locationData changes (page navigation, new search), so it
    // is memoized off locationData.
    const interactiveLayerIds = React.useMemo(
        () =>
            locationData
                .filter((item) => item.type === "geojson" && item.geoJson)
                .map((item) => `geojson-fill-${item.id}`),
        [locationData]
    );

    // Map-level click handler. When a click lands on one of the GeoJSON fill
    // layers, look up the matching item and surface the same popup that
    // point markers use. Using a single map-level handler (rather than per-
    // layer handlers, which react-map-gl/maplibre doesn't expose for Layer)
    // keeps polygon hit-testing consistent with native MapLibre events.
    const handleMapClick = (event: any) => {
        const features = event?.features as Array<any> | undefined;
        if (!features || features.length === 0) return;
        for (const feature of features) {
            const layerId: string | undefined = feature?.layer?.id;
            if (!layerId || !layerId.startsWith("geojson-fill-")) continue;
            const itemId = layerId.replace("geojson-fill-", "");
            const item = locationData.find((it) => it.id === itemId);
            if (item) {
                setSelectedItem(item);
                return;
            }
        }
    };

    // Pointer-cursor affordance over polygon fills so users discover the
    // click target without ambiguity. We toggle the canvas cursor directly
    // because react-map-gl's `cursor` prop only supports a static value.
    const handleMapMouseEnter = () => {
        const map = mapRef.current?.getMap();
        if (map) map.getCanvas().style.cursor = "pointer";
    };
    const handleMapMouseLeave = () => {
        const map = mapRef.current?.getMap();
        if (map) map.getCanvas().style.cursor = "";
    };

    // Calculate bounds and fit map when location data changes. Considers BOTH
    // point markers and polygon/line shapes so a results page that contains only
    // GeoJSON shapes (no points) still gets framed correctly. Previously this
    // only inspected points, leaving the map stuck on a default San Francisco
    // viewport whenever every result was polygon-based.
    useEffect(() => {
        if (!mapRef.current || locationData.length === 0) return;

        const allLats: number[] = [];
        const allLons: number[] = [];

        // Walk arbitrary nested coordinate arrays from a GeoJSON geometry.
        const collectCoords = (coords: any) => {
            if (!Array.isArray(coords)) return;
            if (typeof coords[0] === "number" && typeof coords[1] === "number") {
                allLons.push(coords[0]);
                allLats.push(coords[1]);
                return;
            }
            for (const c of coords) collectCoords(c);
        };

        locationData.forEach((item) => {
            if (
                item.type === "point" &&
                item.latitude !== undefined &&
                item.longitude !== undefined
            ) {
                allLats.push(item.latitude);
                allLons.push(item.longitude);
                return;
            }
            if (item.type === "geojson" && item.geoJson) {
                const geom =
                    item.geoJson?.type === "Feature" ? item.geoJson.geometry : item.geoJson;
                if (geom?.coordinates) collectCoords(geom.coordinates);
                if (geom?.geometries) {
                    // GeometryCollection
                    for (const sub of geom.geometries) {
                        if (sub?.coordinates) collectCoords(sub.coordinates);
                    }
                }
            }
        });

        if (allLats.length === 0) return;

        const minLat = Math.min(...allLats);
        const maxLat = Math.max(...allLats);
        const minLon = Math.min(...allLons);
        const maxLon = Math.max(...allLons);

        const padding = 0.01;
        const bounds: LngLatBoundsLike = [
            [minLon - padding, minLat - padding],
            [maxLon + padding, maxLat + padding],
        ];

        mapRef.current.fitBounds(bounds, {
            padding: 40,
            duration: 1000,
            // For a single point, fitBounds collapses to a tiny bbox; cap the zoom
            // so we don't slam to street-level on a single result.
            maxZoom: 14,
        });
    }, [locationData]);

    // Handle pagination
    const handlePageChange = (pageIndex: number) => {
        dispatch({
            type: "query-paginate",
            pagination: {
                from: (pageIndex - 1) * pageSize,
                size: pageSize,
            },
        });
    };

    const mapStyleUrl = config?.locationServiceApiUrl;

    if (!mapStyleUrl) {
        return (
            <Box padding="l" textAlign="center">
                <Box variant="h2" color="text-status-inactive">
                    Map view is not available
                </Box>
                <Box variant="p" color="text-status-inactive">
                    Location services are not configured for this environment.
                </Box>
            </Box>
        );
    }

    // Calculate initial view state from the first piece of location data we can
    // anchor to -- a point if available, otherwise the first vertex of the first
    // GeoJSON shape. fitBounds (above) immediately reframes once the data loads,
    // but this seeds the initial render so we're already in roughly the right
    // region. Falls back to a neutral world view if nothing has coordinates.
    const initialViewState = (() => {
        for (const item of locationData) {
            if (
                item.type === "point" &&
                item.latitude !== undefined &&
                item.longitude !== undefined
            ) {
                return { latitude: item.latitude, longitude: item.longitude, zoom: 11 };
            }
            if (item.type === "geojson" && item.geoJson) {
                const geom =
                    item.geoJson?.type === "Feature" ? item.geoJson.geometry : item.geoJson;
                let firstCoord: [number, number] | null = null;
                const findFirst = (coords: any): boolean => {
                    if (!Array.isArray(coords)) return false;
                    if (typeof coords[0] === "number" && typeof coords[1] === "number") {
                        firstCoord = [coords[0], coords[1]];
                        return true;
                    }
                    for (const c of coords) {
                        if (findFirst(c)) return true;
                    }
                    return false;
                };
                if (geom?.coordinates) findFirst(geom.coordinates);
                if (firstCoord) {
                    return {
                        latitude: (firstCoord as [number, number])[1],
                        longitude: (firstCoord as [number, number])[0],
                        zoom: 6,
                    };
                }
            }
        }
        return { latitude: 0, longitude: 0, zoom: 2 };
    })();

    return (
        <SpaceBetween direction="vertical" size="m">
            {/* Header with result count */}
            {state?.result?.hits?.total?.value ? (
                <Box variant="h2">
                    {locationData.length} of {state.result.hits.total.value} result
                    {state.result.hits.total.value !== 1 ? "s" : ""} with location data
                </Box>
            ) : (
                <Box variant="h2">No search results with location data</Box>
            )}

            {/* Warning when no location data */}
            {locationData.length === 0 && state?.result?.hits?.total?.value > 0 && (
                <Box variant="p" color="text-status-warning">
                    No results have valid location data. The map view recognizes the following
                    metadata field types:
                    <ul>
                        <li>
                            A <strong>location</strong> metadata field containing an LLA object
                            (e.g. <code>{`{"lat":..,"long":..,"alt":..}`}</code>), a GeoPoint (e.g.{" "}
                            <code>{`{"type":"Point","coordinates":[lon,lat]}`}</code>), or any
                            GeoJSON Geometry / Feature / FeatureCollection (Point, Polygon,
                            MultiPolygon).
                        </li>
                        <li>
                            Or individual <strong>latitude</strong>, <strong>longitude</strong>, and
                            optional <strong>altitude</strong> metadata fields (numeric or string
                            values).
                        </li>
                    </ul>
                </Box>
            )}

            {/* Pagination controls */}
            {pageCount > 1 && (
                <Box textAlign="center">
                    <Pagination
                        currentPageIndex={currentPage}
                        pagesCount={pageCount}
                        onChange={({ detail }) => handlePageChange(detail.currentPageIndex)}
                        ariaLabels={{
                            nextPageLabel: "Next page",
                            previousPageLabel: "Previous page",
                            pageLabel: (pageNumber) => `Page ${pageNumber} of ${pageCount}`,
                        }}
                    />
                </Box>
            )}

            {/* Map */}
            <Map
                ref={mapRef}
                initialViewState={initialViewState}
                style={{ height: "70vh", width: "100%" }}
                mapStyle={mapStyleUrl}
                validateStyle={false}
                interactiveLayerIds={interactiveLayerIds}
                onClick={handleMapClick}
                onMouseEnter={handleMapMouseEnter}
                onMouseLeave={handleMapMouseLeave}
            >
                <NavigationControl position="bottom-right" showZoom showCompass={false} />

                {/* Render GeoJSON features (polygons, etc.). Each result gets a
                    deterministic color from a palette so multiple polygons are
                    visually distinct. Selected item still uses the highlight color
                    and a thicker outline so it stays foregrounded. */}
                {locationData
                    .filter((item) => item.type === "geojson" && item.geoJson)
                    .map((item) => {
                        const isSelected = selectedItem?.id === item.id;
                        const baseColor = colorForKey(item.id);
                        const fillColor = isSelected ? "#0972d3" : baseColor;
                        const lineColor = isSelected ? "#0972d3" : baseColor;
                        return (
                            <Source
                                key={item.id}
                                id={`geojson-source-${item.id}`}
                                type="geojson"
                                data={item.geoJson}
                            >
                                <Layer
                                    id={`geojson-fill-${item.id}`}
                                    type="fill"
                                    paint={{
                                        "fill-color": fillColor,
                                        "fill-opacity": isSelected ? 0.45 : 0.3,
                                    }}
                                />
                                <Layer
                                    id={`geojson-outline-${item.id}`}
                                    type="line"
                                    paint={{
                                        "line-color": lineColor,
                                        "line-width": isSelected ? 3 : 2,
                                    }}
                                />
                            </Source>
                        );
                    })}

                {/* Render point markers */}
                {locationData
                    .filter((item) => item.type === "point")
                    .map((item) => (
                        <Marker
                            key={item.id}
                            latitude={item.latitude!}
                            longitude={item.longitude!}
                            anchor="bottom"
                            onClick={(e) => {
                                e.originalEvent.stopPropagation();
                                setSelectedItem(item);
                            }}
                        >
                            <div
                                style={{
                                    width: "30px",
                                    height: "30px",
                                    cursor: "pointer",
                                    transform: "translate(-50%, -100%)",
                                }}
                            >
                                <svg
                                    viewBox="0 0 24 24"
                                    fill={selectedItem?.id === item.id ? "#0972d3" : "#d91515"}
                                    xmlns="http://www.w3.org/2000/svg"
                                >
                                    <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
                                </svg>
                            </div>
                        </Marker>
                    ))}

                {/* Popup for the selected item — shown for both point markers and
                    polygon (GeoJSON) results. Polygon clicks anchor the popup at
                    the centroid of the shape's bounding box. */}
                {selectedItem &&
                    (() => {
                        let popupLat: number | undefined;
                        let popupLon: number | undefined;
                        if (selectedItem.type === "point") {
                            popupLat = selectedItem.latitude;
                            popupLon = selectedItem.longitude;
                        } else if (selectedItem.type === "geojson") {
                            const anchor = computeGeoJsonPopupAnchor(selectedItem);
                            if (anchor) {
                                popupLon = anchor[0];
                                popupLat = anchor[1];
                            }
                        }
                        if (popupLat === undefined || popupLon === undefined) return null;

                        return (
                            <Popup
                                latitude={popupLat}
                                longitude={popupLon}
                                anchor="top"
                                onClose={() => setSelectedItem(null)}
                                closeButton={true}
                                closeOnClick={false}
                                maxWidth="400px"
                            >
                                <div
                                    style={{
                                        padding: "12px",
                                        minWidth: "300px",
                                        maxWidth: "400px",
                                        backgroundColor: "var(--vams-bg-primary, #ffffff)",
                                        color: "var(--vams-text-primary, #000716)",
                                        borderRadius: "8px",
                                    }}
                                >
                                    <SpaceBetween direction="vertical" size="s">
                                        {/* Preview thumbnail if enabled */}
                                        {state.showPreviewThumbnails && (
                                            <Box textAlign="center">
                                                <PreviewThumbnailCell
                                                    assetId={selectedItem.assetId}
                                                    databaseId={selectedItem.databaseId}
                                                    onOpenFullPreview={() => {}}
                                                    assetName={selectedItem.assetName}
                                                />
                                            </Box>
                                        )}

                                        {/* Heading row — for assets the asset name; for files the file path
                                            (so users immediately see what file they clicked). The metadata
                                            and explanation icons sit alongside. */}
                                        <Box>
                                            <Box variant="awsui-key-label">
                                                {selectedItem.rectype === "file"
                                                    ? "File"
                                                    : "Asset Name"}
                                            </Box>
                                            <SpaceBetween direction="horizontal" size="xs">
                                                <Box variant="h3">
                                                    <span
                                                        style={{
                                                            wordBreak: "break-all",
                                                            whiteSpace: "normal",
                                                        }}
                                                    >
                                                        {selectedItem.assetName}
                                                    </span>
                                                </Box>
                                                {selectedItem.explanation && (
                                                    <ExplanationPopover
                                                        explanation={selectedItem.explanation}
                                                    />
                                                )}
                                                {((selectedItem.metadata &&
                                                    selectedItem.metadata.length > 0) ||
                                                    (selectedItem.attributes &&
                                                        selectedItem.attributes.length > 0)) && (
                                                    <MetadataPopover
                                                        metadata={selectedItem.metadata || []}
                                                        attributes={selectedItem.attributes || []}
                                                    />
                                                )}
                                            </SpaceBetween>
                                        </Box>

                                        {/* Shape type indicator for GeoJSON results */}
                                        {selectedItem.type === "geojson" &&
                                            selectedItem.geoJson && (
                                                <Box>
                                                    <Box variant="awsui-key-label">Shape</Box>
                                                    <Box>
                                                        {(selectedItem.geoJson?.type === "Feature"
                                                            ? selectedItem.geoJson.geometry?.type
                                                            : selectedItem.geoJson?.type) ||
                                                            "Geometry"}
                                                    </Box>
                                                </Box>
                                            )}

                                        {/* Parent asset name (file results only). Links to the parent
                                            asset detail page so users can navigate up from the file. */}
                                        {selectedItem.rectype === "file" &&
                                            selectedItem.parentAssetName && (
                                                <Box>
                                                    <Box variant="awsui-key-label">Asset Name</Box>
                                                    <Link
                                                        href={`#/databases/${selectedItem.databaseId}/assets/${selectedItem.assetId}`}
                                                    >
                                                        {selectedItem.parentAssetName}
                                                    </Link>
                                                </Box>
                                            )}

                                        {/* File extension */}
                                        {selectedItem.rectype === "file" &&
                                            selectedItem.fileExt && (
                                                <Box>
                                                    <Box variant="awsui-key-label">Type</Box>
                                                    <Box>{selectedItem.fileExt}</Box>
                                                </Box>
                                            )}

                                        {/* File size */}
                                        {selectedItem.rectype === "file" &&
                                            selectedItem.fileSize !== undefined && (
                                                <Box>
                                                    <Box variant="awsui-key-label">Size</Box>
                                                    <Box>
                                                        {formatFileSizeForDisplay(
                                                            selectedItem.fileSize
                                                        )}
                                                    </Box>
                                                </Box>
                                            )}

                                        {/* Created / uploaded date */}
                                        {selectedItem.dateCreated && (
                                            <Box>
                                                <Box variant="awsui-key-label">Created</Box>
                                                <Box>
                                                    {(() => {
                                                        try {
                                                            return new Date(
                                                                selectedItem.dateCreated
                                                            ).toLocaleString();
                                                        } catch {
                                                            return selectedItem.dateCreated;
                                                        }
                                                    })()}
                                                </Box>
                                            </Box>
                                        )}

                                        {/* Database */}
                                        <Box>
                                            <Box variant="awsui-key-label">Database</Box>
                                            <Link
                                                href={`#/databases/${selectedItem.databaseId}/assets/`}
                                            >
                                                {selectedItem.databaseId}
                                            </Link>
                                        </Box>

                                        {/* Description */}
                                        {selectedItem.description && (
                                            <Box>
                                                <Box variant="awsui-key-label">Description</Box>
                                                <Box>{selectedItem.description}</Box>
                                            </Box>
                                        )}

                                        {/* Tags */}
                                        {selectedItem.tags && selectedItem.tags.length > 0 && (
                                            <Box>
                                                <Box variant="awsui-key-label">Tags</Box>
                                                <Box>{selectedItem.tags.join(", ")}</Box>
                                            </Box>
                                        )}

                                        {/* View Details Button. For file results we mirror the
                                            list-view file-path link: encode the file path in both
                                            the href (`?filePath=`) and the router state. The href
                                            is what right-click → "Open in new tab" copies, so it
                                            must carry the path so a fresh page load lands on the
                                            correct file inside the file manager. */}
                                        {selectedItem.rectype === "file" ? (
                                            (() => {
                                                const filePathQuery = selectedItem.fileKey
                                                    ? `?filePath=${encodeURIComponent(
                                                          selectedItem.fileKey
                                                      )}`
                                                    : "";
                                                return (
                                                    <Link
                                                        href={`#/databases/${selectedItem.databaseId}/assets/${selectedItem.assetId}${filePathQuery}`}
                                                        onFollow={(event) => {
                                                            event.preventDefault();
                                                            navigate(
                                                                `/databases/${selectedItem.databaseId}/assets/${selectedItem.assetId}${filePathQuery}`,
                                                                {
                                                                    state: {
                                                                        filePathToNavigate:
                                                                            selectedItem.fileKey,
                                                                    },
                                                                }
                                                            );
                                                        }}
                                                    >
                                                        <Button variant="primary" fullWidth>
                                                            View Asset File Details
                                                        </Button>
                                                    </Link>
                                                );
                                            })()
                                        ) : (
                                            <Link
                                                href={`#/databases/${selectedItem.databaseId}/assets/${selectedItem.assetId}`}
                                            >
                                                <Button variant="primary" fullWidth>
                                                    View Asset Details
                                                </Button>
                                            </Link>
                                        )}
                                    </SpaceBetween>
                                </div>
                            </Popup>
                        );
                    })()}
            </Map>

            {/* Informational note about location requirements */}
            <Box variant="p" color="text-body-secondary">
                <Icon name="status-info" /> <strong>Note:</strong> Results appear on the map if they
                have a {`"location"`} metadata field (LLA, GeoPoint, or GeoJSON — Point, Polygon,
                MultiPolygon) or separate {`"latitude"`}, {`"longitude"`}, and optional{" "}
                {`"altitude"`} metadata fields (numeric or string values).
            </Box>
        </SpaceBetween>
    );
}

export default SearchPageMapView;
