/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useRef, useState } from "react";
import Map, { MapMouseEvent, MapRef, Marker, NavigationControl } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import {
    Alert,
    Box,
    Button,
    ButtonGroup,
    FormField,
    Header,
    Input,
    Select,
    SpaceBetween,
} from "@cloudscape-design/components";
import { appCache } from "../../../services/appCache";
import { featuresEnabled } from "../../../common/constants/featuresEnabled";

/** Metadata types this picker supports. */
export type MapPickerType = "lla" | "geopoint" | "geojson";

/** GeoJSON shape kinds we can author. */
type GeoJsonShape = "Point" | "Polygon" | "MultiPolygon";

interface MapMetadataPickerProps {
    type: MapPickerType;
    value: string;
    onChange: (value: string) => void;
    disabled?: boolean;
}

/** Default fallback view when there is no current value. */
const DEFAULT_VIEW = { latitude: 0, longitude: 0, zoom: 2 };

/**
 * Palette used to color individual rings in the picker preview. The order is
 * deterministic (ring index → color) so the user sees stable colors as they
 * add or remove polygons from a MultiPolygon.
 */
const RING_COLOR_PALETTE = [
    "#0972d3", // blue (matches the legacy single-shape outline)
    "#037f0c", // green
    "#9c27b0", // purple
    "#e8810c", // orange
    "#d91515", // red
    "#0d6c8a", // teal
    "#c4318f", // magenta
    "#5a4ab2", // indigo
    "#1f7a1f", // forest
    "#b76e00", // amber
];

const colorForRing = (index: number): string =>
    RING_COLOR_PALETTE[index % RING_COLOR_PALETTE.length];

const isFiniteNumber = (val: any): val is number => typeof val === "number" && isFinite(val);

/** Convert the current metadata value into a [lon, lat] point if one is encoded. */
const parsePoint = (raw: string): [number, number] | null => {
    if (!raw || !raw.trim()) return null;
    try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
            // LLA: {"lat": .., "long": .., "alt": ..}
            if (isFiniteNumber(parsed.lat) && isFiniteNumber(parsed.long)) {
                return [parsed.long, parsed.lat];
            }
            // GeoJSON Point
            if (parsed.type === "Point" && Array.isArray(parsed.coordinates)) {
                const [lon, lat] = parsed.coordinates;
                if (isFiniteNumber(lon) && isFiniteNumber(lat)) return [lon, lat];
            }
            // GeoJSON Feature wrapping a Point
            if (parsed.type === "Feature" && parsed.geometry?.type === "Point") {
                const [lon, lat] = parsed.geometry.coordinates ?? [];
                if (isFiniteNumber(lon) && isFiniteNumber(lat)) return [lon, lat];
            }
        }
    } catch {
        return null;
    }
    return null;
};

/**
 * GeoJSON linear rings are closed (first === last). When we round-trip a
 * committed value back into authoring state, strip the trailing closing
 * vertex so the in-memory ring is "open" -- otherwise the next user click
 * gets appended AFTER the closing duplicate and our sanitizer (which truncates
 * at any mid-ring repeat of the first vertex) silently drops the new click.
 */
const stripClosingVertex = (ring: number[][]): number[][] => {
    if (ring.length < 2) return ring;
    const first = ring[0];
    const last = ring[ring.length - 1];
    if (first[0] === last[0] && first[1] === last[1]) {
        return ring.slice(0, -1);
    }
    return ring;
};

/** Extract polygon rings from a GeoJSON Polygon / MultiPolygon value, if any. */
const parsePolygonRings = (raw: string): { kind: GeoJsonShape; rings: number[][][] } | null => {
    if (!raw || !raw.trim()) return null;
    try {
        const parsed = JSON.parse(raw);
        const geom = parsed?.type === "Feature" ? parsed.geometry : parsed;
        if (!geom?.type) return null;
        if (geom.type === "Polygon" && Array.isArray(geom.coordinates)) {
            return {
                kind: "Polygon",
                rings: geom.coordinates.map((r: number[][]) => stripClosingVertex(r)),
            };
        }
        if (geom.type === "MultiPolygon" && Array.isArray(geom.coordinates)) {
            // Flatten outer rings of each polygon for editing; keeps holes simple.
            const rings: number[][][] = [];
            for (const poly of geom.coordinates) {
                if (Array.isArray(poly) && Array.isArray(poly[0])) {
                    rings.push(stripClosingVertex(poly[0]));
                }
            }
            return { kind: "MultiPolygon", rings };
        }
    } catch {
        return null;
    }
    return null;
};

/**
 * Build the JSON value the parent receives based on the picker mode.
 *  - lla       -> {"lat","long","alt"}
 *  - geopoint  -> {"type":"Point","coordinates":[lon,lat]}
 *  - geojson Point/Polygon/MultiPolygon
 */
const buildValue = (
    type: MapPickerType,
    shape: GeoJsonShape,
    point: [number, number] | null,
    rings: number[][][],
    altitude: number
): string => {
    if (type === "lla") {
        if (!point) return "";
        return JSON.stringify({ lat: point[1], long: point[0], alt: altitude });
    }
    if (type === "geopoint") {
        if (!point) return "";
        return JSON.stringify({ type: "Point", coordinates: [point[0], point[1]] });
    }
    // geojson
    if (shape === "Point") {
        if (!point) return "";
        return JSON.stringify({ type: "Point", coordinates: [point[0], point[1]] });
    }
    // Polygon / MultiPolygon need at least one ring with >=3 unique points + closure.
    // Sanitize each ring before closing: drop consecutive duplicate vertices and any
    // mid-ring duplicate of the first vertex (a misclick on the starting point would
    // otherwise produce a self-intersecting linear ring that OpenSearch rejects with
    // 'invalid_shape_exception'). The final ring is closed by repeating the first
    // vertex once at the end.
    const sanitize = (r: number[][]): number[][] => {
        const out: number[][] = [];
        for (const v of r) {
            const prev = out[out.length - 1];
            if (prev && prev[0] === v[0] && prev[1] === v[1]) continue; // skip consecutive dup
            out.push(v);
        }
        if (out.length < 3) return [];
        const first = out[0];
        // Strip any mid-ring duplicate of the first vertex (treat that as "user wanted
        // to close here" -- ignore everything after).
        for (let i = 1; i < out.length; i++) {
            if (out[i][0] === first[0] && out[i][1] === first[1]) {
                return out.slice(0, i + 1);
            }
        }
        return out;
    };

    const closed = rings
        .map(sanitize)
        .filter((r) => r.length >= 3)
        .map((r) => {
            const first = r[0];
            const last = r[r.length - 1];
            if (first[0] !== last[0] || first[1] !== last[1]) return [...r, first];
            return r;
        });
    if (closed.length === 0) return "";
    if (shape === "Polygon") {
        return JSON.stringify({ type: "Polygon", coordinates: [closed[0]] });
    }
    return JSON.stringify({
        type: "MultiPolygon",
        coordinates: closed.map((r) => [r]),
    });
};

const SHAPE_OPTIONS = [
    { label: "Point", value: "Point" },
    { label: "Polygon (single shape)", value: "Polygon" },
    { label: "MultiPolygon (multiple shapes)", value: "MultiPolygon" },
];

/**
 * Reusable map editor that lives inside the metadata complex-edit modal.
 * Lets users click the map to set a point or build a polygon and writes the
 * result back to the parent in the JSON shape the metadata type expects.
 */
const MapMetadataPicker: React.FC<MapMetadataPickerProps> = ({
    type,
    value,
    onChange,
    disabled = false,
}) => {
    const config = appCache.getItem("config");
    const locationServicesEnabled = config?.featuresEnabled?.includes(
        featuresEnabled.LOCATIONSERVICES
    );
    const mapStyleUrl = config?.locationServiceApiUrl;

    const mapRef = useRef<MapRef>(null);

    // Authoring state. We seed it from the current value and then drive the
    // textual side of the editor on every mutation.
    const initialPoint = useMemo(() => parsePoint(value), [value]);
    const initialRings = useMemo(() => parsePolygonRings(value), [value]);

    const [shape, setShape] = useState<GeoJsonShape>(() => {
        if (type !== "geojson") return "Point";
        if (initialRings) return initialRings.kind;
        return "Point";
    });

    const [point, setPoint] = useState<[number, number] | null>(initialPoint);
    const [rings, setRings] = useState<number[][][]>(initialRings?.rings ?? []);
    const [activeRing, setActiveRing] = useState<number>(0);
    // Gate <Source>/<Layer> rendering on the map's load event. Otherwise the
    // Source can mount before map.style._loaded is true (common when the
    // editor opens inside a Cloudscape Modal), createSource() returns null,
    // and the GeoJSON layers are never registered with MapLibre — the
    // polygon fill / outline never appear even though vertex markers do.
    const [mapLoaded, setMapLoaded] = useState(false);

    // For LLA we also let the user keep an altitude; the map can't capture it.
    const [altitude, setAltitude] = useState<number>(() => {
        if (type !== "lla") return 0;
        try {
            const parsed = JSON.parse(value || "{}");
            return isFiniteNumber(parsed.alt) ? parsed.alt : 0;
        } catch {
            return 0;
        }
    });

    // Re-seed when the parent rewrites the value (e.g. user types in the text editor).
    useEffect(() => {
        const nextPoint = parsePoint(value);
        const nextRings = parsePolygonRings(value);
        setPoint(nextPoint);
        if (nextRings) {
            setRings(nextRings.rings);
            if (type === "geojson") setShape(nextRings.kind);
        } else if (type === "geojson" && shape !== "Point" && !nextPoint) {
            // Switching shapes resets rings; honor the user's mode selection instead of
            // clobbering it just because the value didn't parse as a polygon.
        } else if (!nextPoint) {
            setRings([]);
        }
        if (type === "lla") {
            try {
                const parsed = JSON.parse(value || "{}");
                if (isFiniteNumber(parsed.alt)) setAltitude(parsed.alt);
            } catch {
                /* ignore */
            }
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value, type]);

    // Initial map view: prefer the current value's centroid, fall back to default.
    const initialViewState = useMemo(() => {
        if (initialPoint)
            return { latitude: initialPoint[1], longitude: initialPoint[0], zoom: 10 };
        if (initialRings && initialRings.rings.length > 0 && initialRings.rings[0].length > 0) {
            const [lon, lat] = initialRings.rings[0][0];
            return { latitude: lat, longitude: lon, zoom: 8 };
        }
        return DEFAULT_VIEW;
    }, [initialPoint, initialRings]);

    // Fit bounds whenever rings change AND the map has loaded. Without the
    // mapLoaded gate, the effect runs on first mount with the saved rings
    // already populated but before the map's style finishes loading —
    // MapLibre silently no-ops fitBounds in that window, leaving the view
    // anchored on the initial vertex from initialViewState. Re-running once
    // mapLoaded flips true reframes around the full polygon. Manual bbox
    // computation avoids importing LngLatBounds from maplibre-gl.
    useEffect(() => {
        if (!mapLoaded) return;
        const map = mapRef.current?.getMap();
        if (!map || rings.length === 0) return;
        try {
            let minLon = Infinity;
            let minLat = Infinity;
            let maxLon = -Infinity;
            let maxLat = -Infinity;
            let any = false;
            for (const ring of rings) {
                for (const coord of ring) {
                    if (typeof coord[0] !== "number" || typeof coord[1] !== "number") continue;
                    if (coord[0] < minLon) minLon = coord[0];
                    if (coord[1] < minLat) minLat = coord[1];
                    if (coord[0] > maxLon) maxLon = coord[0];
                    if (coord[1] > maxLat) maxLat = coord[1];
                    any = true;
                }
            }
            if (any) {
                // Guard against zero-area bounds (single click): MapLibre throws
                // "Map cannot fit within canvas with the given bounds, padding..."
                // when the bbox has no extent. easeTo to the centroid instead.
                if (minLon === maxLon && minLat === maxLat) {
                    map.easeTo({ center: [minLon, minLat], zoom: 12, duration: 200 });
                } else {
                    map.fitBounds(
                        [
                            [minLon, minLat],
                            [maxLon, maxLat],
                        ],
                        { padding: 40, duration: 200, maxZoom: 14 }
                    );
                }
            }
        } catch {
            /* ignore */
        }
    }, [rings, mapLoaded]);

    /** Push the current authoring state up to the parent as a JSON string. */
    const commit = (
        nextShape: GeoJsonShape,
        nextPoint: [number, number] | null,
        nextRings: number[][][],
        nextAltitude: number
    ) => {
        onChange(buildValue(type, nextShape, nextPoint, nextRings, nextAltitude));
    };

    const handleMapClick = (e: MapMouseEvent) => {
        if (disabled) return;
        const lon = e.lngLat.lng;
        const lat = e.lngLat.lat;

        if (type === "lla" || type === "geopoint" || (type === "geojson" && shape === "Point")) {
            const next: [number, number] = [lon, lat];
            setPoint(next);
            commit(shape, next, rings, altitude);
            return;
        }

        // Polygon / MultiPolygon: append vertex to the active ring.
        const updatedRings = [...rings];
        if (!updatedRings[activeRing]) updatedRings[activeRing] = [];
        updatedRings[activeRing] = [...updatedRings[activeRing], [lon, lat]];
        setRings(updatedRings);
        commit(shape, point, updatedRings, altitude);
    };

    const handleShapeChange = (next: GeoJsonShape) => {
        setShape(next);
        // Reset authoring state to avoid mixing point/polygon data.
        setPoint(null);
        setRings([]);
        setActiveRing(0);
        commit(next, null, [], altitude);
    };

    const removeLastVertex = () => {
        if (rings.length === 0) return;
        const updated = rings.map((r, i) => (i === activeRing ? r.slice(0, -1) : r));
        // Drop empty trailing rings to keep state tidy.
        while (updated.length > 0 && updated[updated.length - 1].length === 0) updated.pop();
        setRings(updated);
        if (activeRing >= updated.length && activeRing > 0) setActiveRing(updated.length - 1);
        commit(shape, point, updated, altitude);
    };

    const startNewPolygon = () => {
        const updated = [...rings, []];
        setRings(updated);
        setActiveRing(updated.length - 1);
        commit(shape, point, updated, altitude);
    };

    const clearAll = () => {
        setPoint(null);
        setRings([]);
        setActiveRing(0);
        commit(shape, null, [], altitude);
    };

    /**
     * Single FeatureCollection covering every ring's preview. Each Feature
     * carries `kind` (line | polygon), `ringIndex`, `color`, `isActive`,
     * `fillOpacity`, and `lineWidth` properties so the global Layers below
     * can paint each feature differently via data-driven expressions.
     *
     * Why one FeatureCollection instead of one <Source> per ring:
     * @vis.gl/react-maplibre 8 has a race where conditionally-mounted
     * <Source> components can race the map's style-load lifecycle and silently
     * fail to register their underlying layers. A single, always-mounted
     * Source registered on initial mount sidesteps the race entirely; the
     * Source just receives setData() updates as the user clicks.
     *
     * - 0 or 1 vertices  → no feature emitted (vertex markers cover this)
     * - 2 vertices       → emitted as a LineString so the in-progress edge is visible
     * - 3+ vertices      → emitted as a closed Polygon (fill + outline)
     */
    const overlayFeatures = useMemo(() => {
        if (type !== "geojson" || shape === "Point") {
            return { type: "FeatureCollection" as const, features: [] };
        }
        const features: any[] = [];
        rings.forEach((r, idx) => {
            if (r.length < 2) return;
            const isActive = idx === activeRing;
            const color = colorForRing(idx);
            if (r.length === 2) {
                features.push({
                    type: "Feature",
                    properties: {
                        kind: "line",
                        ringIndex: idx,
                        color,
                        isActive,
                        lineWidth: isActive ? 3 : 2,
                    },
                    geometry: { type: "LineString", coordinates: r },
                });
                return;
            }
            const first = r[0];
            const last = r[r.length - 1];
            const closed = first[0] !== last[0] || first[1] !== last[1] ? [...r, first] : r;
            features.push({
                type: "Feature",
                properties: {
                    kind: "polygon",
                    ringIndex: idx,
                    color,
                    isActive,
                    fillOpacity: isActive ? 0.55 : 0.4,
                    lineWidth: isActive ? 3 : 2,
                },
                geometry: { type: "Polygon", coordinates: [closed] },
            });
        });
        return { type: "FeatureCollection" as const, features };
    }, [type, shape, rings, activeRing]);

    /**
     * Imperatively manage the overlay source and layers on the underlying
     * MapLibre instance. See GeoFilterMapSelector for why we don't use the
     * declarative <Source>/<Layer> path.
     */
    const SOURCE_ID = "map-picker-overlay";
    useEffect(() => {
        const map = mapRef.current?.getMap?.();
        if (!map || !mapLoaded) return;

        const ensureLayers = () => {
            try {
                if (!map.getSource(SOURCE_ID)) {
                    map.addSource(SOURCE_ID, {
                        type: "geojson",
                        data: { type: "FeatureCollection", features: [] },
                    });
                }
                const layers: any[] = [
                    {
                        id: `${SOURCE_ID}-fill`,
                        type: "fill",
                        source: SOURCE_ID,
                        filter: ["==", ["get", "kind"], "polygon"],
                        paint: {
                            "fill-color": ["get", "color"],
                            "fill-opacity": ["get", "fillOpacity"],
                        },
                    },
                    {
                        id: `${SOURCE_ID}-outline-polygon`,
                        type: "line",
                        source: SOURCE_ID,
                        filter: ["==", ["get", "kind"], "polygon"],
                        paint: {
                            "line-color": ["get", "color"],
                            "line-width": ["get", "lineWidth"],
                        },
                    },
                    {
                        id: `${SOURCE_ID}-outline-line`,
                        type: "line",
                        source: SOURCE_ID,
                        filter: ["==", ["get", "kind"], "line"],
                        paint: {
                            "line-color": ["get", "color"],
                            "line-width": ["get", "lineWidth"],
                            "line-dasharray": [2, 2],
                        },
                    },
                ];
                for (const layer of layers) {
                    if (!map.getLayer(layer.id)) {
                        map.addLayer(layer);
                    }
                }
            } catch (err) {
                console.warn("MetadataPicker overlay layer setup error:", err);
            }
        };

        ensureLayers();

        try {
            const src = map.getSource(SOURCE_ID) as any;
            if (src && typeof src.setData === "function") {
                src.setData(overlayFeatures);
            }
        } catch (err) {
            console.warn("MetadataPicker overlay setData error:", err);
        }
    }, [overlayFeatures, mapLoaded]);

    if (!locationServicesEnabled) {
        return (
            <Alert type="info" header="Map editor unavailable">
                The map editor requires the Location Services feature to be enabled. You can still
                type or paste values directly.
            </Alert>
        );
    }

    if (!mapStyleUrl) {
        return (
            <Alert type="warning" header="Map style URL missing">
                Map editor cannot load — the deployment did not provide a Location Service style
                URL. Use the textual editor instead.
            </Alert>
        );
    }

    const isPointMode = type !== "geojson" || shape === "Point";

    return (
        <SpaceBetween direction="vertical" size="s">
            <Header
                variant="h3"
                description={
                    isPointMode
                        ? "Click the map to set the point. Click again to move it."
                        : "Click the map to add vertices. Use the buttons below to manage the shape."
                }
            >
                Map editor
            </Header>

            {type === "geojson" && (
                <FormField label="Shape">
                    <Select
                        disabled={disabled}
                        selectedOption={
                            SHAPE_OPTIONS.find((o) => o.value === shape) ?? SHAPE_OPTIONS[0]
                        }
                        options={SHAPE_OPTIONS}
                        onChange={({ detail }) =>
                            handleShapeChange(detail.selectedOption.value as GeoJsonShape)
                        }
                    />
                </FormField>
            )}

            <div
                style={{
                    width: "100%",
                    height: "420px",
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
                    interactive={!disabled}
                    attributionControl={false}
                    onClick={handleMapClick}
                    onLoad={() => setMapLoaded(true)}
                    cursor={disabled ? "default" : "crosshair"}
                    // Force mercator: maplibre-gl 5.x defaults to globe under
                    // some style URLs, which breaks easeTo around a single point
                    // ("Easing around a point is not supported under globe").
                    projection={{ type: "mercator" } as any}
                >
                    <NavigationControl position="top-right" showZoom showCompass={false} />

                    {/* Overlay source/layers are added imperatively via the
                        useEffect above. The declarative <Source>/<Layer>
                        path was racing the AWS Location Service style-load
                        lifecycle and silently failing to register layers,
                        so we attach them directly to the GL instance once
                        it has fired its 'load' event. */}

                    {/* Vertex markers for polygon authoring. Each ring's vertices use that ring's
                        color; the active ring's vertices are larger so the user knows which
                        ring their next click extends. */}
                    {type === "geojson" &&
                        shape !== "Point" &&
                        rings.flatMap((ring, ringIdx) => {
                            const isActive = ringIdx === activeRing;
                            const ringColor = colorForRing(ringIdx);
                            return ring.map((coord, vIdx) => (
                                <Marker
                                    key={`v-${ringIdx}-${vIdx}`}
                                    longitude={coord[0]}
                                    latitude={coord[1]}
                                    anchor="center"
                                >
                                    <div
                                        style={{
                                            width: isActive ? "12px" : "10px",
                                            height: isActive ? "12px" : "10px",
                                            borderRadius: "50%",
                                            background: ringColor,
                                            border: "2px solid white",
                                            boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
                                        }}
                                    />
                                </Marker>
                            ));
                        })}

                    {/* Single-point marker for LLA / GeoPoint / GeoJSON Point */}
                    {isPointMode && point && (
                        <Marker longitude={point[0]} latitude={point[1]} anchor="bottom">
                            <div style={{ width: "24px", height: "24px" }}>
                                <svg viewBox="0 0 24 24" fill="#0972d3">
                                    <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
                                </svg>
                            </div>
                        </Marker>
                    )}
                </Map>
            </div>

            {/* Toolbar — left: active-ring badge + grouped actions, right: destructive Clear.
                Background is left transparent so the bar inherits the modal/page surface in
                both light and dark mode. The border + radius give it the "toolbar" look. */}
            <div
                style={{
                    display: "flex",
                    flexWrap: "wrap",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "12px",
                    padding: "8px 12px",
                    border: "1px solid var(--color-border-divider-default, #e9ebed)",
                    borderRadius: "8px",
                }}
            >
                <div
                    style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}
                >
                    {type === "geojson" && shape === "MultiPolygon" && rings.length > 0 && (
                        <div
                            aria-label={`Editing polygon ${activeRing + 1} of ${rings.length}`}
                            style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: "6px",
                                padding: "2px 10px",
                                borderRadius: "999px",
                                border: "1px solid var(--color-border-divider-default, #e9ebed)",
                                fontSize: "12px",
                                fontWeight: 500,
                            }}
                        >
                            <span
                                style={{
                                    display: "inline-block",
                                    width: "10px",
                                    height: "10px",
                                    borderRadius: "50%",
                                    background: colorForRing(activeRing),
                                }}
                            />
                            Editing polygon {activeRing + 1} of {rings.length}
                        </div>
                    )}

                    {type === "geojson" && shape !== "Point" && (
                        <ButtonGroup
                            ariaLabel="Polygon edit actions"
                            variant="icon"
                            items={
                                shape === "MultiPolygon"
                                    ? [
                                          {
                                              type: "icon-button",
                                              id: "remove-vertex",
                                              iconName: "remove",
                                              text: "Remove last vertex",
                                              disabled:
                                                  disabled ||
                                                  rings.length === 0 ||
                                                  (rings[activeRing]?.length ?? 0) === 0,
                                          },
                                          {
                                              type: "icon-button",
                                              id: "new-polygon",
                                              iconName: "add-plus",
                                              text: "Start new polygon",
                                              disabled: disabled,
                                          },
                                      ]
                                    : [
                                          {
                                              type: "icon-button",
                                              id: "remove-vertex",
                                              iconName: "remove",
                                              text: "Remove last vertex",
                                              disabled:
                                                  disabled ||
                                                  rings.length === 0 ||
                                                  (rings[activeRing]?.length ?? 0) === 0,
                                          },
                                      ]
                            }
                            onItemClick={({ detail }) => {
                                if (detail.id === "remove-vertex") removeLastVertex();
                                else if (detail.id === "new-polygon") startNewPolygon();
                            }}
                        />
                    )}
                </div>

                <Button
                    onClick={clearAll}
                    disabled={disabled || (!point && rings.length === 0)}
                    iconName="close"
                >
                    Clear map
                </Button>
            </div>

            {/* LLA gets a separate altitude slot since the map can only capture lon/lat. */}
            {type === "lla" && (
                <FormField
                    label="Altitude (meters)"
                    description="Set independently — the map only captures latitude and longitude."
                >
                    <Input
                        type="number"
                        step={undefined as any}
                        value={String(altitude)}
                        disabled={disabled}
                        onChange={({ detail }) => {
                            const next = parseFloat(detail.value);
                            const safe = isFiniteNumber(next) ? next : 0;
                            setAltitude(safe);
                            commit(shape, point, rings, safe);
                        }}
                        ariaLabel="Altitude (meters)"
                    />
                </FormField>
            )}

            {type === "geojson" &&
                shape !== "Point" &&
                rings[activeRing]?.length > 0 &&
                rings[activeRing].length < 3 && (
                    <Box color="text-status-warning" fontSize="body-s">
                        A polygon needs at least 3 vertices before it is committed as a value.
                    </Box>
                )}
        </SpaceBetween>
    );
};

export default MapMetadataPicker;
