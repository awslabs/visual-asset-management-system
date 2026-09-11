/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useRef, useEffect, useState } from "react";
import Map, { Marker, NavigationControl, MapRef, Source, Layer } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { Box } from "@cloudscape-design/components";
import { extractLocationData } from "../utils/locationUtils";
import { offsetForKey, splitGeoJsonForColoring } from "../utils/polygonColor";
import MapPreviewModal from "./MapPreviewModal";

interface MapThumbnailProps {
    assetData: any;
    mapStyleUrl: string;
    width?: number;
    height?: number;
    defaultZoom?: number;
    /** Click the thumbnail to open a larger map modal. Default: true. */
    enableExpand?: boolean;
    /** Header text shown in the expanded map modal. */
    expandHeader?: string;
    /**
     * Stable identifier (asset/file/database id) used to derive a deterministic
     * polygon color so adjacent thumbnails are visually distinguishable. Single
     * points keep the default red marker regardless of this key.
     */
    colorKey?: string;
}

const MapThumbnail: React.FC<MapThumbnailProps> = ({
    assetData,
    mapStyleUrl,
    width = 200,
    height = 150,
    defaultZoom = 2,
    enableExpand = true,
    expandHeader,
    colorKey,
}) => {
    // Stagger sub-polygon colors using the optional colorKey so adjacent
    // thumbnails in a list still look distinct from each other.
    const baseColorOffset = offsetForKey(colorKey);
    const mapRef = useRef<MapRef>(null);
    const [isLoaded, setIsLoaded] = useState(false);
    const [isModalVisible, setIsModalVisible] = useState(false);

    // Extract location data from asset
    const locationData = extractLocationData(assetData);

    // Determine initial view state based on location type
    const getInitialViewState = () => {
        if (
            locationData &&
            locationData.type === "point" &&
            locationData.latitude &&
            locationData.longitude
        ) {
            return {
                latitude: locationData.latitude,
                longitude: locationData.longitude,
                zoom: defaultZoom,
            };
        }
        // For GeoJSON, use a default center (will be adjusted by fitBounds)
        return {
            latitude: 0,
            longitude: 0,
            zoom: 2,
        };
    };

    // Fit bounds for GeoJSON features. Computed by manually tracking min/max
    // [lon, lat] across the geometry, then handing fitBounds a tuple. Avoids
    // depending on maplibre-gl's LngLatBounds class symbol -- importing it
    // from the ESM bundle hits a Vite/__publicField interop crash on some
    // browsers and the class isn't attached to window.maplibregl in v5.
    useEffect(() => {
        if (
            !isLoaded ||
            !mapRef.current ||
            !locationData ||
            locationData.type !== "geojson" ||
            !locationData.geoJson
        ) {
            return;
        }
        try {
            let minLon = Infinity;
            let minLat = Infinity;
            let maxLon = -Infinity;
            let maxLat = -Infinity;
            let any = false;

            const collect = (coords: any) => {
                if (!Array.isArray(coords)) return;
                if (typeof coords[0] === "number" && typeof coords[1] === "number") {
                    if (coords[0] < minLon) minLon = coords[0];
                    if (coords[1] < minLat) minLat = coords[1];
                    if (coords[0] > maxLon) maxLon = coords[0];
                    if (coords[1] > maxLat) maxLat = coords[1];
                    any = true;
                    return;
                }
                for (const c of coords) collect(c);
            };

            const geom =
                locationData.geoJson.type === "Feature"
                    ? locationData.geoJson.geometry
                    : locationData.geoJson;
            if (geom?.coordinates) collect(geom.coordinates);

            if (any) {
                const map = mapRef.current.getMap();
                if (minLon === maxLon && minLat === maxLat) {
                    map.easeTo({ center: [minLon, minLat], zoom: 10, duration: 0 });
                } else {
                    map.fitBounds(
                        [
                            [minLon, minLat],
                            [maxLon, maxLat],
                        ],
                        { padding: 20, duration: 0, maxZoom: 14 }
                    );
                }
            }
        } catch (error) {
            console.warn("Error fitting bounds for GeoJSON:", error);
        }
    }, [isLoaded, locationData]);

    // If no valid location data, don't render anything
    if (!locationData) {
        return null;
    }

    const containerStyle: React.CSSProperties = {
        width: `${width}px`,
        height: `${height}px`,
        border: "1px solid #e0e0e0",
        borderRadius: "4px",
        overflow: "hidden",
        position: "relative",
        cursor: enableExpand ? "pointer" : "default",
    };

    // Wrapping the inner map in a click overlay (rather than wiring onClick on the
    // map itself) keeps the click target consistent for both Point and GeoJSON
    // layers and prevents the underlying MapLibre handlers from swallowing it.
    const handleOpen = () => {
        if (enableExpand) {
            setIsModalVisible(true);
        }
    };

    return (
        <Box>
            <div
                role={enableExpand ? "button" : undefined}
                tabIndex={enableExpand ? 0 : undefined}
                aria-label={enableExpand ? "Open larger map preview" : undefined}
                onClick={handleOpen}
                onKeyDown={(e) => {
                    if (enableExpand && (e.key === "Enter" || e.key === " ")) {
                        e.preventDefault();
                        handleOpen();
                    }
                }}
                style={containerStyle}
            >
                <Map
                    ref={mapRef}
                    initialViewState={getInitialViewState()}
                    style={{ width: "100%", height: "100%" }}
                    mapStyle={mapStyleUrl}
                    validateStyle={false}
                    onLoad={() => setIsLoaded(true)}
                    interactive={!enableExpand}
                    attributionControl={false}
                >
                    {!enableExpand && (
                        <NavigationControl position="top-right" showZoom showCompass={false} />
                    )}

                    {/* Split a multi-polygon (or geometry collection) value into individual
                        sub-shapes so each polygon gets its own color. Each sub-geometry is
                        wrapped in a GeoJSON Feature so MapLibre's geojson source accepts it
                        across all engine versions (some are strict about bare geometries). */}
                    {locationData &&
                        locationData.type === "geojson" &&
                        locationData.geoJson &&
                        splitGeoJsonForColoring(locationData.geoJson, baseColorOffset).map(
                            (sub) => (
                                <Source
                                    key={`thumb-${sub.id}`}
                                    id={`geojson-thumb-${sub.id}`}
                                    type="geojson"
                                    data={
                                        {
                                            type: "Feature",
                                            geometry: sub.geometry,
                                            properties: {},
                                        } as any
                                    }
                                >
                                    <Layer
                                        id={`geojson-thumb-fill-${sub.id}`}
                                        type="fill"
                                        paint={{
                                            "fill-color": sub.color,
                                            "fill-opacity": 0.35,
                                        }}
                                    />
                                    <Layer
                                        id={`geojson-thumb-outline-${sub.id}`}
                                        type="line"
                                        paint={{
                                            "line-color": sub.color,
                                            "line-width": 2,
                                        }}
                                    />
                                </Source>
                            )
                        )}

                    {/* Render point marker */}
                    {locationData &&
                        locationData.type === "point" &&
                        locationData.latitude &&
                        locationData.longitude && (
                            <Marker
                                latitude={locationData.latitude}
                                longitude={locationData.longitude}
                                anchor="bottom"
                            >
                                <div
                                    style={{
                                        width: "20px",
                                        height: "20px",
                                    }}
                                >
                                    <svg
                                        viewBox="0 0 24 24"
                                        fill="#d91515"
                                        xmlns="http://www.w3.org/2000/svg"
                                    >
                                        <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
                                    </svg>
                                </div>
                            </Marker>
                        )}
                </Map>
            </div>
            {enableExpand && isModalVisible && (
                <MapPreviewModal
                    visible={isModalVisible}
                    onDismiss={() => setIsModalVisible(false)}
                    locationData={locationData}
                    mapStyleUrl={mapStyleUrl}
                    header={expandHeader}
                    colorKey={colorKey}
                    // Match the thumbnail's zoom so opening the modal feels like a
                    // zoomed-in view of the same map (not a fresh street-level pin).
                    defaultZoom={defaultZoom}
                />
            )}
        </Box>
    );
};

export default MapThumbnail;
