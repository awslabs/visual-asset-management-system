/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import Modal from "@cloudscape-design/components/modal";
import Box from "@cloudscape-design/components/box";
import Map, { Layer, MapRef, Marker, NavigationControl, Source } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { LocationData } from "../utils/locationUtils";
import { offsetForKey, splitGeoJsonForColoring } from "../utils/polygonColor";

interface MapPreviewModalProps {
    visible: boolean;
    onDismiss: () => void;
    locationData: LocationData;
    mapStyleUrl: string;
    header?: string;
    /** Default zoom level used for point-only locations. */
    defaultZoom?: number;
    /**
     * Optional stable identifier so the polygon color in the expanded preview
     * matches the corresponding mini-map thumbnail.
     */
    colorKey?: string;
}

const MAP_HEIGHT_PX = 540;

const MapPreviewModal: React.FC<MapPreviewModalProps> = ({
    visible,
    onDismiss,
    locationData,
    mapStyleUrl,
    header,
    defaultZoom = 2,
    colorKey,
}) => {
    const baseColorOffset = offsetForKey(colorKey);
    const mapRef = useRef<MapRef>(null);
    const [isLoaded, setIsLoaded] = useState(false);

    const initialViewState =
        locationData.type === "point" &&
        locationData.latitude !== undefined &&
        locationData.longitude !== undefined
            ? {
                  latitude: locationData.latitude,
                  longitude: locationData.longitude,
                  zoom: defaultZoom,
              }
            : { latitude: 0, longitude: 0, zoom: 2 };

    // Fit bounds for GeoJSON shapes once the modal map has loaded. Manual
    // bbox computation avoids importing LngLatBounds from maplibre-gl, which
    // crashes under Vite with '__publicField is not defined' on some ESM paths.
    useEffect(() => {
        if (
            !visible ||
            !isLoaded ||
            !mapRef.current ||
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
            const geoJson = locationData.geoJson as any;
            const geom = geoJson.type === "Feature" ? geoJson.geometry : geoJson;
            if (geom?.coordinates) collect(geom.coordinates);
            if (any) {
                const map = mapRef.current.getMap();
                if (minLon === maxLon && minLat === maxLat) {
                    map.easeTo({ center: [minLon, minLat], zoom: 12, duration: 0 });
                } else {
                    map.fitBounds(
                        [
                            [minLon, minLat],
                            [maxLon, maxLat],
                        ],
                        { padding: 40, duration: 0, maxZoom: 16 }
                    );
                }
            }
        } catch (error) {
            console.warn("Error fitting bounds for GeoJSON in MapPreviewModal:", error);
        }
    }, [visible, isLoaded, locationData]);

    // Reset load state every time the modal closes so re-opening triggers fitBounds again.
    useEffect(() => {
        if (!visible) {
            setIsLoaded(false);
        }
    }, [visible]);

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={header || "Map preview"}
            size="large"
            closeAriaLabel="Close map preview"
        >
            <Box>
                <div
                    style={{
                        width: "100%",
                        height: `${MAP_HEIGHT_PX}px`,
                        border: "1px solid var(--color-border-divider-default, #e0e0e0)",
                        borderRadius: "4px",
                        overflow: "hidden",
                    }}
                >
                    <Map
                        ref={mapRef}
                        initialViewState={initialViewState}
                        style={{ width: "100%", height: "100%" }}
                        mapStyle={mapStyleUrl}
                        validateStyle={false}
                        onLoad={() => setIsLoaded(true)}
                        interactive={true}
                        attributionControl={false}
                    >
                        <NavigationControl position="top-right" showZoom showCompass={false} />

                        {locationData.type === "geojson" &&
                            locationData.geoJson &&
                            splitGeoJsonForColoring(locationData.geoJson, baseColorOffset).map(
                                (sub) => (
                                    <Source
                                        key={`preview-${sub.id}`}
                                        id={`map-preview-modal-${sub.id}`}
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
                                            id={`map-preview-modal-fill-${sub.id}`}
                                            type="fill"
                                            paint={{
                                                "fill-color": sub.color,
                                                "fill-opacity": 0.35,
                                            }}
                                        />
                                        <Layer
                                            id={`map-preview-modal-outline-${sub.id}`}
                                            type="line"
                                            paint={{
                                                "line-color": sub.color,
                                                "line-width": 2,
                                            }}
                                        />
                                    </Source>
                                )
                            )}

                        {locationData.type === "point" &&
                            locationData.latitude !== undefined &&
                            locationData.longitude !== undefined && (
                                <Marker
                                    latitude={locationData.latitude}
                                    longitude={locationData.longitude}
                                    anchor="bottom"
                                >
                                    <div style={{ width: "28px", height: "28px" }}>
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
        </Modal>
    );
};

export default MapPreviewModal;
