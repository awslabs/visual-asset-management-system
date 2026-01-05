/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export interface LocationData {
    type: "point" | "geojson";
    // For points
    latitude?: number;
    longitude?: number;
    // For GeoJSON
    geoJson?: any;
}

/**
 * Extract location data from a search result item
 * Returns either a point (lat/lon) or GeoJSON data
 */
export const extractLocationData = (item: any): LocationData | null => {
    try {
        // Check if MD_ exists as an object (new format)
        if (item.MD_ && typeof item.MD_ === "object" && !Array.isArray(item.MD_)) {
            const md = item.MD_;

            // Priority 1: Check for 'location' field in MD_ object (case-insensitive)
            // This field should contain a JSON object with longitude, latitude, and optionally altitude
            let locationField: any;
            Object.keys(md).forEach((key) => {
                if (key.toLowerCase() === "location") {
                    locationField = md[key];
                }
            });

            if (locationField) {
                try {
                    const locationData =
                        typeof locationField === "string"
                            ? JSON.parse(locationField)
                            : locationField;

                    // Check if it has longitude and latitude properties (case-insensitive)
                    // Support both full names (longitude/latitude) and LLA format (long/lat)
                    let lon: any;
                    let lat: any;

                    Object.keys(locationData).forEach((key) => {
                        const keyLower = key.toLowerCase();
                        if (keyLower === "latitude" || keyLower === "lat") {
                            lat = locationData[key];
                        }
                        if (keyLower === "longitude" || keyLower === "lon" || keyLower === "long") {
                            lon = locationData[key];
                        }
                    });

                    if (lon !== undefined && lat !== undefined) {
                        const longitude = typeof lon === "number" ? lon : parseFloat(lon);
                        const latitude = typeof lat === "number" ? lat : parseFloat(lat);

                        if (
                            !isNaN(latitude) &&
                            !isNaN(longitude) &&
                            latitude >= -90 &&
                            latitude <= 90 &&
                            longitude >= -180 &&
                            longitude <= 180
                        ) {
                            return { type: "point", latitude, longitude };
                        }
                    }

                    // Check if location is GeoJSON
                    if (locationData.type) {
                        // If it's a GeoJSON Point, extract coordinates
                        if (
                            locationData.type === "Point" &&
                            Array.isArray(locationData.coordinates)
                        ) {
                            const [lon, lat] = locationData.coordinates;
                            if (
                                typeof lat === "number" &&
                                typeof lon === "number" &&
                                !isNaN(lat) &&
                                !isNaN(lon)
                            ) {
                                return { type: "point", latitude: lat, longitude: lon };
                            }
                        }

                        // If it's a Feature with Point geometry
                        if (
                            locationData.type === "Feature" &&
                            locationData.geometry?.type === "Point"
                        ) {
                            const [lon, lat] = locationData.geometry.coordinates;
                            if (
                                typeof lat === "number" &&
                                typeof lon === "number" &&
                                !isNaN(lat) &&
                                !isNaN(lon)
                            ) {
                                return { type: "point", latitude: lat, longitude: lon };
                            }
                        }

                        // For any other GeoJSON (Polygon, MultiPolygon, etc.), pass it through
                        if (
                            locationData.type === "Feature" ||
                            locationData.type === "Polygon" ||
                            locationData.type === "MultiPolygon"
                        ) {
                            return { type: "geojson", geoJson: locationData };
                        }
                    }
                } catch (parseError) {
                    console.warn("Error parsing location field from MD_:", parseError);
                    // Continue to fallback methods
                }
            }

            // Priority 2: Check for latitude/longitude fields in MD_ object (case-insensitive)
            let latValue: any;
            let lonValue: any;

            Object.keys(md).forEach((key) => {
                const keyLower = key.toLowerCase();
                if (keyLower === "latitude" || keyLower === "lat") latValue = md[key];
                if (keyLower === "longitude" || keyLower === "lon" || keyLower === "long")
                    lonValue = md[key];
            });

            if (latValue !== undefined && lonValue !== undefined) {
                const lat = typeof latValue === "number" ? latValue : parseFloat(latValue);
                const lon = typeof lonValue === "number" ? lonValue : parseFloat(lonValue);

                if (
                    !isNaN(lat) &&
                    !isNaN(lon) &&
                    lat >= -90 &&
                    lat <= 90 &&
                    lon >= -180 &&
                    lon <= 180
                ) {
                    return { type: "point", latitude: lat, longitude: lon };
                }
            }
        }

        return null;
    } catch (error) {
        console.warn("Error extracting location data:", error);
        return null;
    }
};
