/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useRef } from "react";
import Map, { Marker, Popup, NavigationControl, MapRef, Source, Layer } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { SearchPageViewProps } from "./SearchPageTypes";
import { Box, Button, Link, Pagination, SpaceBetween, Popover, Icon } from "@cloudscape-design/components";
import { Cache } from "aws-amplify";
import { LngLatBoundsLike } from "maplibre-gl";
import PreviewThumbnailCell from "../../components/search/SearchPreviewThumbnail/PreviewThumbnailCell";
import { SearchExplanation } from "../../components/search/types";

interface LocationData {
    id: string;
    type: 'point' | 'geojson';
    databaseId: string;
    assetId: string;
    assetName: string;
    description?: string;
    tags?: string[];
    explanation?: SearchExplanation;
    metadata?: Array<{name: string, type: string, value: any}>;
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
                        <Box variant="h5">Matched Fields ({explanation.matched_fields.length}):</Box>
                        <Box>
                            <ul style={{ margin: 0, paddingLeft: '20px' }}>
                                {explanation.matched_fields.slice(0, 5).map((field, idx) => (
                                    <li key={idx}>
                                        <strong>{field}:</strong> {explanation.match_reasons[field] || 'Matched'}
                                    </li>
                                ))}
                                {explanation.matched_fields.length > 5 && (
                                    <li>...and {explanation.matched_fields.length - 5} more fields</li>
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

// Helper component to render metadata popover
const MetadataPopover: React.FC<{ metadata: Array<{name: string, type: string, value: any}> }> = ({ metadata }) => {
    if (metadata.length === 0) {
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
                    <Box variant="h4">Metadata Fields ({metadata.length})</Box>
                    <Box>
                        <ul style={{ margin: 0, paddingLeft: '20px' }}>
                            {metadata.map((field, idx) => (
                                <li key={idx}>
                                    <strong>{field.name} ({field.type}):</strong> {String(field.value)}
                                </li>
                            ))}
                        </ul>
                    </Box>
                </SpaceBetween>
            }
        >
            <Icon name="status-info" variant="link" />
        </Popover>
    );
};

// Helper function to extract and format metadata fields with type information
const extractMetadata = (item: any): Array<{name: string, type: string, value: any}> => {
    const metadata: Array<{name: string, type: string, value: any}> = [];
    
    // Type mapping for display
    const typeLabels: Record<string, string> = {
        'str': 'String',
        'num': 'Number',
        'bool': 'Boolean',
        'date': 'Date',
        'list': 'List',
        'gp': 'Geo Point',
        'gs': 'Geo Shape',
    };
    
    // Find all MD_ fields
    Object.keys(item).forEach(key => {
        if (key.startsWith('MD_')) {
            // Format: MD_<type>_<fieldname>
            const parts = key.split('_');
            if (parts.length >= 3) {
                // parts[0] = 'MD', parts[1] = type, parts[2+] = field name
                const fieldType = parts[1];
                const fieldName = parts.slice(2).join('_');
                metadata.push({
                    name: fieldName,
                    type: typeLabels[fieldType] || fieldType,
                    value: item[key]
                });
            } else {
                // Fallback: just remove MD_ prefix
                metadata.push({
                    name: key.substring(3),
                    type: 'Unknown',
                    value: item[key]
                });
            }
        }
    });
    
    return metadata;
};

/**
 * Extract location data from a search result item
 * Returns either a point (lat/lon) or GeoJSON data
 */
const extractLocationData = (item: any): Partial<LocationData> | null => {
    try {
        // Priority 1: Check for MD_gp_location or MD_gs_location (metadata geo fields)
        const geoData = item.MD_gp_location || item.MD_gs_location || item.gp_location;
        
        if (geoData) {
            // Parse if string
            const parsed = typeof geoData === 'string' ? JSON.parse(geoData) : geoData;
            
            // If it's a GeoJSON Point, extract coordinates
            if (parsed.type === 'Point' && Array.isArray(parsed.coordinates)) {
                const [lon, lat] = parsed.coordinates;
                if (typeof lat === 'number' && typeof lon === 'number' && !isNaN(lat) && !isNaN(lon)) {
                    return { type: 'point', latitude: lat, longitude: lon };
                }
            }
            
            // If it's a Feature with Point geometry
            if (parsed.type === 'Feature' && parsed.geometry?.type === 'Point') {
                const [lon, lat] = parsed.geometry.coordinates;
                if (typeof lat === 'number' && typeof lon === 'number' && !isNaN(lat) && !isNaN(lon)) {
                    return { type: 'point', latitude: lat, longitude: lon };
                }
            }
            
            // For any other GeoJSON (Polygon, MultiPolygon, etc.), pass it through
            if (parsed.type === 'Feature' || parsed.type === 'Polygon' || parsed.type === 'MultiPolygon') {
                return { type: 'geojson', geoJson: parsed };
            }
            
            // Handle legacy direct lat/lon object
            if (typeof parsed.lat === 'number' && typeof parsed.lon === 'number') {
                if (!isNaN(parsed.lat) && !isNaN(parsed.lon)) {
                    return { type: 'point', latitude: parsed.lat, longitude: parsed.lon };
                }
            }
        }
        
        // Priority 2: Check for string/number latitude/longitude metadata fields
        let latValue: any;
        let lonValue: any;
        
        Object.keys(item).forEach(key => {
            if (key.match(/MD_(str|num)_latitude/i)) latValue = item[key];
            if (key.match(/MD_(str|num)_longitude/i)) lonValue = item[key];
        });
        
        if (latValue !== undefined && lonValue !== undefined) {
            const lat = typeof latValue === 'number' ? latValue : parseFloat(latValue);
            const lon = typeof lonValue === 'number' ? lonValue : parseFloat(lonValue);
            
            if (!isNaN(lat) && !isNaN(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
                return { type: 'point', latitude: lat, longitude: lon };
            }
        }
        
        return null;
    } catch (error) {
        console.warn('Error extracting location data:', error);
        return null;
    }
};

function SearchPageMapView({ state, dispatch }: SearchPageViewProps) {
    const [selectedItem, setSelectedItem] = useState<LocationData | null>(null);
    const mapRef = useRef<MapRef>(null);
    const config = Cache.getItem("config");
    
    // Get pagination info from state
    const pageSize = state.tablePreferences?.pageSize || 50;
    const currentPage = 1 + Math.floor((state.pagination?.from || 0) / pageSize);
    const totalResults = state.result?.hits?.total?.value || 0;
    const pageCount = Math.ceil(totalResults / pageSize);

    // Extract location data from search results
    const locationData: LocationData[] = React.useMemo(() => {
        if (!state.result?.hits?.hits) return [];

        const validData: LocationData[] = [];

        state.result.hits.hits.forEach((hit: any) => {
            const source = hit._source;
            if (!source) return;

            const location = extractLocationData(source);
            if (location && location.type) {
                validData.push({
                    ...location,
                    id: hit._id,
                    databaseId: source.str_databaseid,
                    assetId: source.str_assetid,
                    assetName: source.str_assetname || "Unnamed Asset",
                    description: source.str_description,
                    tags: source.list_tags,
                    explanation: hit.explanation,
                    metadata: extractMetadata(source),
                } as LocationData);
            }
        });

        console.log(`[MapView] Extracted ${validData.length} items with location data from ${state.result.hits.hits.length} results`);
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

            locationData.forEach(item => {
                if (item.type === 'point' && item.latitude && item.longitude) {
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
            type: 'query-paginate',
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
    const firstPoint = locationData.find(item => item.type === 'point');
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
                    {locationData.length} of {state.result.hits.total.value} result{state.result.hits.total.value !== 1 ? 's' : ''} with location data
                </Box>
            ) : (
                <Box variant="h2">
                    No search results with location data
                </Box>
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
                            pageLabel: (pageNumber) => `Page ${pageNumber} of ${pageCount}`
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
                    .filter(item => item.type === 'geojson' && item.geoJson)
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
                                    'fill-color': selectedItem?.id === item.id ? '#0972d3' : '#d91515',
                                    'fill-opacity': 0.3,
                                }}
                            />
                            <Layer
                                id={`geojson-outline-${item.id}`}
                                type="line"
                                paint={{
                                    'line-color': selectedItem?.id === item.id ? '#0972d3' : '#d91515',
                                    'line-width': 2,
                                }}
                            />
                        </Source>
                    ))}

                {/* Render point markers */}
                {locationData
                    .filter(item => item.type === 'point')
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
                {selectedItem && selectedItem.type === 'point' && (
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
                                        {selectedItem.explanation && <ExplanationPopover explanation={selectedItem.explanation} />}
                                        {selectedItem.metadata && selectedItem.metadata.length > 0 && <MetadataPopover metadata={selectedItem.metadata} />}
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
        </SpaceBetween>
    );
}

export default SearchPageMapView;
