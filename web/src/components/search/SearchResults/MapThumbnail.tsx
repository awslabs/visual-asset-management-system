/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useRef, useEffect, useState } from "react";
import Map, { Marker, NavigationControl, MapRef, Source, Layer } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { Box } from "@cloudscape-design/components";
import { extractLocationData } from "../utils/locationUtils";

interface MapThumbnailProps {
    assetData: any;
    mapStyleUrl: string;
    width?: number;
    height?: number;
    defaultZoom?: number;
}

const MapThumbnail: React.FC<MapThumbnailProps> = ({
    assetData,
    mapStyleUrl,
    width = 200,
    height = 150,
    defaultZoom = 13,
}) => {
    const mapRef = useRef<MapRef>(null);
    const [isLoaded, setIsLoaded] = useState(false);

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

    // Fit bounds for GeoJSON features
    useEffect(() => {
        if (
            isLoaded &&
            mapRef.current &&
            locationData &&
            locationData.type === "geojson" &&
            locationData.geoJson
        ) {
            try {
                const map = mapRef.current.getMap();
                const bounds = new (window as any).maplibregl.LngLatBounds();

                // Helper to add coordinates to bounds
                const addCoordinatesToBounds = (coords: any) => {
                    if (Array.isArray(coords[0])) {
                        coords.forEach((coord: any) => addCoordinatesToBounds(coord));
                    } else {
                        bounds.extend(coords);
                    }
                };

                // Extract coordinates based on GeoJSON type
                if (locationData.geoJson.type === "Feature") {
                    const geometry = locationData.geoJson.geometry;
                    if (geometry && geometry.coordinates) {
                        addCoordinatesToBounds(geometry.coordinates);
                    }
                } else if (locationData.geoJson.coordinates) {
                    addCoordinatesToBounds(locationData.geoJson.coordinates);
                }

                if (!bounds.isEmpty()) {
                    map.fitBounds(bounds, { padding: 20, duration: 0 });
                }
            } catch (error) {
                console.warn("Error fitting bounds for GeoJSON:", error);
            }
        }
    }, [isLoaded, locationData]);

    // If no valid location data, don't render anything
    if (!locationData) {
        return null;
    }

    return (
        <Box>
            <div
                style={{
                    width: `${width}px`,
                    height: `${height}px`,
                    border: "1px solid #e0e0e0",
                    borderRadius: "4px",
                    overflow: "hidden",
                }}
            >
                <Map
                    ref={mapRef}
                    initialViewState={getInitialViewState()}
                    style={{ width: "100%", height: "100%" }}
                    mapStyle={mapStyleUrl}
                    validateStyle={false}
                    onLoad={() => setIsLoaded(true)}
                    interactive={true}
                    attributionControl={false}
                >
                    <NavigationControl position="top-right" showZoom showCompass={false} />

                    {/* Render GeoJSON features (polygons, etc.) */}
                    {locationData && locationData.type === "geojson" && locationData.geoJson && (
                        <Source id="geojson-source" type="geojson" data={locationData.geoJson}>
                            <Layer
                                id="geojson-fill"
                                type="fill"
                                paint={{
                                    "fill-color": "#d91515",
                                    "fill-opacity": 0.3,
                                }}
                            />
                            <Layer
                                id="geojson-outline"
                                type="line"
                                paint={{
                                    "line-color": "#d91515",
                                    "line-width": 2,
                                }}
                            />
                        </Source>
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
        </Box>
    );
};

export default MapThumbnail;
