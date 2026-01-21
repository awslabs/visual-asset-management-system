/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useRef } from "react";
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
import { Cache } from "aws-amplify";
import { LngLatBoundsLike } from "maplibre-gl";
import PreviewThumbnailCell from "./SearchPreviewThumbnail/PreviewThumbnailCell";
import { SearchExplanation } from "./types";
import { extractLocationData } from "./utils/locationUtils";

interface LocationDataWithDetails {
    id: string;
    type: "point" | "geojson";
    databaseId: string;
    assetId: string;
    assetName: string;
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
    const config = Cache.getItem("config");

    // Get pagination info from state
    const pageSize = state.tablePreferences?.pageSize || 50;
    const currentPage = 1 + Math.floor((state.pagination?.from || 0) / pageSize);
    const totalResults = state.result?.hits?.total?.value || 0;
    const pageCount = Math.ceil(totalResults / pageSize);

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
                validData.push({
                    ...location,
                    id: hit._id,
                    databaseId: source.str_databaseid,
                    assetId: source.str_assetid,
                    assetName: source.str_assetname || "Unnamed Asset",
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
    }, [state.result]);

    // Reset selected items when location data changes (page change or new search)
    useEffect(() => {
        setSelectedItem(null);
    }, [locationData]);

    // Calculate bounds and fit map when location data changes
    useEffect(() => {
        if (mapRef.current && locationData.length > 0) {
            const allLats: number[] = [];
            const allLons: number[] = [];

            locationData.forEach((item) => {
                if (item.type === "point" && item.latitude && item.longitude) {
                    allLats.push(item.latitude);
                    allLons.push(item.longitude);
                }
            });

            if (allLats.length > 0) {
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
                });
            }
        }
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

    // Calculate initial view state from first point
    const firstPoint = locationData.find((item) => item.type === "point");
    const initialViewState = firstPoint
        ? {
              latitude: firstPoint.latitude!,
              longitude: firstPoint.longitude!,
              zoom: 11,
          }
        : {
              latitude: 37.8,
              longitude: -122.4,
              zoom: 11,
          };

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
                    No results have valid location data. Assets need either:
                    <ul>
                        <li>Location metadata field with GeoJSON data</li>
                        <li>Latitude and Longitude metadata fields with valid coordinates</li>
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
            >
                <NavigationControl position="bottom-right" showZoom showCompass={false} />

                {/* Render GeoJSON features (polygons, etc.) */}
                {locationData
                    .filter((item) => item.type === "geojson" && item.geoJson)
                    .map((item) => (
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
                                    "fill-color":
                                        selectedItem?.id === item.id ? "#0972d3" : "#d91515",
                                    "fill-opacity": 0.3,
                                }}
                            />
                            <Layer
                                id={`geojson-outline-${item.id}`}
                                type="line"
                                paint={{
                                    "line-color":
                                        selectedItem?.id === item.id ? "#0972d3" : "#d91515",
                                    "line-width": 2,
                                }}
                            />
                        </Source>
                    ))}

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

                {/* Popup for selected item */}
                {selectedItem && selectedItem.type === "point" && (
                    <Popup
                        latitude={selectedItem.latitude!}
                        longitude={selectedItem.longitude!}
                        anchor="top"
                        onClose={() => setSelectedItem(null)}
                        closeButton={true}
                        closeOnClick={false}
                        maxWidth="400px"
                    >
                        <div style={{ padding: "12px", minWidth: "300px", maxWidth: "400px" }}>
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

                                {/* Asset Name with explanation and metadata icons */}
                                <Box>
                                    <Box variant="awsui-key-label">Asset Name</Box>
                                    <SpaceBetween direction="horizontal" size="xs">
                                        <Box variant="h3">{selectedItem.assetName}</Box>
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

                                {/* Database */}
                                <Box>
                                    <Box variant="awsui-key-label">Database</Box>
                                    <Link href={`#/databases/${selectedItem.databaseId}/assets/`}>
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

                                {/* View Asset Button */}
                                <Link
                                    href={`#/databases/${selectedItem.databaseId}/assets/${selectedItem.assetId}`}
                                >
                                    <Button variant="primary" fullWidth>
                                        View Asset Details
                                    </Button>
                                </Link>
                            </SpaceBetween>
                        </div>
                    </Popup>
                )}
            </Map>

            {/* Informational note about location requirements */}
            <Box variant="p" color="text-body-secondary">
                <Icon name="status-info" /> <strong>Note:</strong> Assets appear on the map if they
                have a <strong>"location"</strong> metadata field (LLA-type with longitude/latitude
                JSON object) or separate <strong>"latitude"</strong> and{" "}
                <strong>"longitude"</strong> metadata fields (string or number type).
            </Box>
        </SpaceBetween>
    );
}

export default SearchPageMapView;
