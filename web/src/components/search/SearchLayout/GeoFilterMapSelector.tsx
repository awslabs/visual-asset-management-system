/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useRef, useState } from "react";
import Modal from "@cloudscape-design/components/modal";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ButtonGroup from "@cloudscape-design/components/button-group";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Map, { MapMouseEvent, MapRef, Marker, NavigationControl } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";
import { GeoSearchFilter } from "../types";

interface GeoFilterMapSelectorProps {
    visible: boolean;
    onDismiss: () => void;
    onApply: (filter: GeoSearchFilter) => void;
    initialFilter: GeoSearchFilter | null;
    mapStyleUrl: string;
}

type SelectorMode = "point" | "bbox" | "polygon";

const RELATION_OPTIONS = [
    { label: "Intersects", value: "intersects" },
    { label: "Within", value: "within" },
    { label: "Contains", value: "contains" },
    { label: "Disjoint", value: "disjoint" },
];

const MAP_HEIGHT_PX = 540;

/** Build a GeoJSON circle ring for a (lon, lat, radiusMeters) input. Used for
 *  visualizing the radius around a point on the map. */
const buildRadiusCircle = (lon: number, lat: number, radiusMeters: number): any => {
    if (!radiusMeters || radiusMeters <= 0) return null;
    const points = 64;
    // Approximate degree-per-meter at this latitude. Good enough for a visual
    // overlay; the backend uses a true geodesic circle when filtering.
    const latRad = (lat * Math.PI) / 180;
    const dLat = radiusMeters / 111320;
    const dLon = radiusMeters / (111320 * Math.cos(latRad) || 1);
    const coords: number[][] = [];
    for (let i = 0; i <= points; i++) {
        const angle = (i / points) * 2 * Math.PI;
        coords.push([lon + dLon * Math.cos(angle), lat + dLat * Math.sin(angle)]);
    }
    return { type: "Polygon", coordinates: [coords] };
};

const buildBboxPolygon = (
    a: [number, number],
    b: [number, number]
): {
    polygon: any;
    topLeft: { lat: number; lon: number };
    bottomRight: { lat: number; lon: number };
} => {
    const lons = [a[0], b[0]].sort((x, y) => x - y);
    const lats = [a[1], b[1]].sort((x, y) => x - y);
    const w = lons[0];
    const e = lons[1];
    const s = lats[0];
    const n = lats[1];
    return {
        polygon: {
            type: "Polygon",
            coordinates: [
                [
                    [w, n],
                    [e, n],
                    [e, s],
                    [w, s],
                    [w, n],
                ],
            ],
        },
        topLeft: { lat: n, lon: w },
        bottomRight: { lat: s, lon: e },
    };
};

/** Seed picker state from an existing filter so re-opening the modal restores edits. */
const deriveInitialState = (
    f: GeoSearchFilter | null
): {
    mode: SelectorMode;
    relation: GeoSearchFilter["relation"];
    point: [number, number] | null;
    radius: string;
    corners: Array<[number, number]>;
    polygon: number[][];
} => {
    const relation = (f?.relation || "intersects") as GeoSearchFilter["relation"];
    if (f?.point) {
        return {
            mode: "point",
            relation,
            point: [f.point.lon, f.point.lat],
            radius: f.point.radiusMeters != null ? String(f.point.radiusMeters) : "",
            corners: [],
            polygon: [],
        };
    }
    if (f?.bbox) {
        return {
            mode: "bbox",
            relation,
            point: null,
            radius: "",
            corners: [
                [f.bbox.topLeft.lon, f.bbox.topLeft.lat],
                [f.bbox.bottomRight.lon, f.bbox.bottomRight.lat],
            ],
            polygon: [],
        };
    }
    if (f?.geoJson?.type === "Polygon" && Array.isArray(f.geoJson.coordinates?.[0])) {
        // Drop the closing duplicate vertex while editing.
        const ring = f.geoJson.coordinates[0] as number[][];
        const open =
            ring.length >= 2 &&
            ring[0][0] === ring[ring.length - 1][0] &&
            ring[0][1] === ring[ring.length - 1][1]
                ? ring.slice(0, -1)
                : ring;
        return {
            mode: "polygon",
            relation,
            point: null,
            radius: "",
            corners: [],
            polygon: open,
        };
    }
    return { mode: "point", relation, point: null, radius: "", corners: [], polygon: [] };
};

/**
 * Map-driven selector for the geospatial search filter. Lets the user click
 * the map to set a point (with optional radius), define a bounding box via
 * two corner clicks, or build an arbitrary polygon. Apply emits a
 * GeoSearchFilter the parent panel forwards to the search request.
 */
const GeoFilterMapSelector: React.FC<GeoFilterMapSelectorProps> = ({
    visible,
    onDismiss,
    onApply,
    initialFilter,
    mapStyleUrl,
}) => {
    // Seed state from the existing filter; re-seeding on modal open is handled by
    // the dedicated effect below.
    const seed = useMemo(() => deriveInitialState(initialFilter), [initialFilter]);

    const [mode, setMode] = useState<SelectorMode>(seed.mode);
    const [relation, setRelation] = useState<GeoSearchFilter["relation"]>(seed.relation);
    const [point, setPoint] = useState<[number, number] | null>(seed.point);
    const [radius, setRadius] = useState<string>(seed.radius);
    const [corners, setCorners] = useState<Array<[number, number]>>(seed.corners);
    const [polygon, setPolygon] = useState<number[][]>(seed.polygon);
    const [error, setError] = useState<string | null>(null);
    // Gate Source/Layer rendering on the map's onLoad event. react-map-gl 8's
    // <Source> component's createSource() returns null if map.style._loaded is
    // false at the moment the component mounts — and although it listens for
    // 'styledata' to retry, the retry only fires if the conditional that
    // mounted the Source re-evaluates. With our preview pattern (the Source
    // only renders once the user has clicked a vertex), the Source can mount
    // before the style is loaded and never re-create its underlying GeoJSON
    // source, so nothing is drawn. Holding back overlay rendering until
    // onLoad fires sidesteps this entire race.
    const [mapLoaded, setMapLoaded] = useState(false);

    const mapRef = useRef<MapRef>(null);

    // Re-seed every time the modal opens. Also reset mapLoaded so the next
    // modal open is treated as "not yet ready" until we either re-detect
    // an already-loaded GL instance synchronously (the common case under
    // Cloudscape's Modal, which hides its body via display:none and thus
    // preserves the underlying MapLibre instance) or wait for a fresh
    // onLoad event (the cold-start case on the very first open).
    useEffect(() => {
        if (!visible) {
            setMapLoaded(false);
            return;
        }
        const next = deriveInitialState(initialFilter);
        setMode(next.mode);
        setRelation(next.relation);
        setPoint(next.point);
        setRadius(next.radius);
        setCorners(next.corners);
        setPolygon(next.polygon);
        setError(null);

        // If the GL instance is preserved from a previous open (Cloudscape's
        // Modal hides via display:none rather than unmounting), `onLoad`
        // won't fire again on this open. Without this re-arm, the
        // setMapLoaded(false) on close above would gate the overlay
        // management effect off forever — leaving the previously-drawn
        // bbox or polygon visible on the map and silently ignoring new
        // map clicks because the overlay source can't be updated.
        // Detect the still-loaded instance and flip mapLoaded back on
        // synchronously so the overlay effect re-runs with the freshly
        // re-seeded data (often empty after a panel-side filter clear).
        const map = mapRef.current?.getMap?.();
        if (map && typeof map.isStyleLoaded === "function" && map.isStyleLoaded()) {
            setMapLoaded(true);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [visible]);

    const handleModeChange = (next: SelectorMode) => {
        setMode(next);
        // Wipe authoring state when switching modes so users don't see stale shapes.
        setPoint(null);
        setRadius("");
        setCorners([]);
        setPolygon([]);
        setError(null);
    };

    const handleMapClick = (e: MapMouseEvent) => {
        const lon = e.lngLat.lng;
        const lat = e.lngLat.lat;
        setError(null);
        if (mode === "point") {
            setPoint([lon, lat]);
            return;
        }
        if (mode === "bbox") {
            // First click sets one corner; second click sets the opposite.
            // A third click starts a fresh box from this point.
            if (corners.length === 0 || corners.length === 2) {
                setCorners([[lon, lat]]);
            } else {
                setCorners([corners[0], [lon, lat]]);
            }
            return;
        }
        // polygon
        setPolygon([...polygon, [lon, lat]]);
    };

    const removeLastVertex = () => {
        if (polygon.length === 0) return;
        setPolygon(polygon.slice(0, -1));
    };

    const clearShape = () => {
        setPoint(null);
        setRadius("");
        setCorners([]);
        setPolygon([]);
        setError(null);
    };

    // GeoJSON overlays for the current authoring state.
    const radiusCircle = useMemo(() => {
        if (mode !== "point" || !point) return null;
        const r = parseFloat(radius);
        if (!Number.isFinite(r) || r <= 0) return null;
        return buildRadiusCircle(point[0], point[1], r);
    }, [mode, point, radius]);

    const bboxPolygon = useMemo(() => {
        if (mode !== "bbox" || corners.length !== 2) return null;
        return buildBboxPolygon(corners[0], corners[1]).polygon;
    }, [mode, corners]);

    /**
     * Single FeatureCollection covering every overlay shape this picker can
     * display (radius circle, bbox rectangle, in-progress polygon line, closed
     * polygon). Each feature is tagged with a `kind` property so the layer
     * filters can route it to the right paint style.
     *
     * Building one stable FeatureCollection — rather than mounting/unmounting
     * separate <Source> components per mode — is essential under @vis.gl/
     * react-maplibre 8: a Source that mounts conditionally (e.g. only after
     * the user has placed 3 vertices) can race the map's style-load lifecycle
     * and createSource() returns null, leaving the child Layers unrendered.
     * A single always-mounted Source registers correctly on initial style
     * load and just receives setData() updates as the user clicks.
     */
    const overlayFeatures = useMemo(() => {
        const features: any[] = [];
        if (mode === "point" && point) {
            const r = parseFloat(radius);
            if (Number.isFinite(r) && r > 0) {
                features.push({
                    type: "Feature",
                    properties: { kind: "radius" },
                    geometry: buildRadiusCircle(point[0], point[1], r),
                });
            }
        }
        if (mode === "bbox" && corners.length === 2) {
            features.push({
                type: "Feature",
                properties: { kind: "bbox" },
                geometry: buildBboxPolygon(corners[0], corners[1]).polygon,
            });
        }
        if (mode === "polygon" && polygon.length >= 2) {
            if (polygon.length === 2) {
                features.push({
                    type: "Feature",
                    properties: { kind: "polyline" },
                    geometry: { type: "LineString", coordinates: polygon },
                });
            } else {
                const first = polygon[0];
                const last = polygon[polygon.length - 1];
                const closed =
                    first[0] !== last[0] || first[1] !== last[1] ? [...polygon, first] : polygon;
                features.push({
                    type: "Feature",
                    properties: { kind: "polygon" },
                    geometry: { type: "Polygon", coordinates: [closed] },
                });
            }
        }
        return { type: "FeatureCollection" as const, features };
    }, [mode, point, radius, corners, polygon]);

    /**
     * Imperatively manage the overlay source and layers on the underlying
     * MapLibre instance. Why imperatively rather than via <Source>/<Layer>:
     * AWS Location Service style URLs come with ~30 layers (background, water,
     * land, roads, labels). When @vis.gl/react-maplibre's <Layer> calls
     * map.addLayer(layer) without a beforeId it should put the layer on top —
     * but in our setup the overlay layers were never appearing on screen,
     * suggesting either a registration race against the late-loading style
     * or a z-order surprise from the basemap. Doing it ourselves with explicit
     * map.addLayer(...) calls inside a 'load' handler eliminates both
     * unknowns: the layers are guaranteed to be added after the basemap and
     * we can verify their presence via map.getLayer(...) if needed.
     */
    const SOURCE_ID = "geo-filter-overlay";
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
                        id: `${SOURCE_ID}-fill-radius`,
                        type: "fill",
                        source: SOURCE_ID,
                        filter: ["==", ["get", "kind"], "radius"],
                        paint: { "fill-color": "#0972d3", "fill-opacity": 0.15 },
                    },
                    {
                        id: `${SOURCE_ID}-fill-bbox`,
                        type: "fill",
                        source: SOURCE_ID,
                        filter: ["==", ["get", "kind"], "bbox"],
                        paint: { "fill-color": "#0972d3", "fill-opacity": 0.18 },
                    },
                    {
                        id: `${SOURCE_ID}-fill-polygon`,
                        type: "fill",
                        source: SOURCE_ID,
                        filter: ["==", ["get", "kind"], "polygon"],
                        paint: { "fill-color": "#0972d3", "fill-opacity": 0.2 },
                    },
                    {
                        id: `${SOURCE_ID}-line-radius`,
                        type: "line",
                        source: SOURCE_ID,
                        filter: ["==", ["get", "kind"], "radius"],
                        paint: {
                            "line-color": "#0972d3",
                            "line-width": 1.5,
                            "line-dasharray": [2, 2],
                        },
                    },
                    {
                        id: `${SOURCE_ID}-line-bbox`,
                        type: "line",
                        source: SOURCE_ID,
                        filter: ["==", ["get", "kind"], "bbox"],
                        paint: { "line-color": "#0972d3", "line-width": 2 },
                    },
                    {
                        id: `${SOURCE_ID}-line-polygon`,
                        type: "line",
                        source: SOURCE_ID,
                        filter: ["==", ["get", "kind"], "polygon"],
                        paint: { "line-color": "#0972d3", "line-width": 2 },
                    },
                    {
                        id: `${SOURCE_ID}-line-polyline`,
                        type: "line",
                        source: SOURCE_ID,
                        filter: ["==", ["get", "kind"], "polyline"],
                        paint: {
                            "line-color": "#0972d3",
                            "line-width": 2,
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
                console.warn("GeoFilter overlay layer setup error:", err);
            }
        };

        ensureLayers();

        // Push the latest features to the source.
        try {
            const src = map.getSource(SOURCE_ID) as any;
            if (src && typeof src.setData === "function") {
                src.setData(overlayFeatures);
            }
        } catch (err) {
            console.warn("GeoFilter overlay setData error:", err);
        }
    }, [overlayFeatures, mapLoaded]);

    // Initial map view: prefer the seed shape's centroid; otherwise default world.
    const initialViewState = useMemo(() => {
        if (seed.point) return { latitude: seed.point[1], longitude: seed.point[0], zoom: 10 };
        if (seed.corners.length === 2) {
            const [a, b] = seed.corners;
            return { latitude: (a[1] + b[1]) / 2, longitude: (a[0] + b[0]) / 2, zoom: 8 };
        }
        if (seed.polygon.length > 0) {
            const [lon, lat] = seed.polygon[0];
            return { latitude: lat, longitude: lon, zoom: 8 };
        }
        return { latitude: 0, longitude: 0, zoom: 2 };
    }, [seed]);

    const apply = () => {
        const baseRelation = relation || "intersects";
        if (mode === "point") {
            if (!point) {
                setError("Click the map to choose a point.");
                return;
            }
            const pointPayload: NonNullable<GeoSearchFilter["point"]> = {
                lat: point[1],
                lon: point[0],
            };
            if (radius.trim() !== "") {
                const r = parseFloat(radius);
                if (!Number.isFinite(r) || r <= 0) {
                    setError("Radius must be a positive number of meters.");
                    return;
                }
                pointPayload.radiusMeters = r;
            }
            onApply({ relation: baseRelation, point: pointPayload });
            return;
        }
        if (mode === "bbox") {
            if (corners.length !== 2) {
                setError("Click two opposite corners to define the bounding box.");
                return;
            }
            const built = buildBboxPolygon(corners[0], corners[1]);
            onApply({
                relation: baseRelation,
                bbox: { topLeft: built.topLeft, bottomRight: built.bottomRight },
            });
            return;
        }
        // polygon
        if (polygon.length < 3) {
            setError("A polygon needs at least 3 vertices.");
            return;
        }
        const first = polygon[0];
        const last = polygon[polygon.length - 1];
        const closed = first[0] !== last[0] || first[1] !== last[1] ? [...polygon, first] : polygon;
        onApply({
            relation: baseRelation,
            geoJson: { type: "Polygon", coordinates: [closed] },
        });
    };

    return (
        <Modal
            visible={visible}
            onDismiss={onDismiss}
            header={
                <Header
                    variant="h2"
                    description="Click the map to define a point, bounding box, or polygon. Apply to use it as the geospatial search filter."
                >
                    Map selector
                </Header>
            }
            size="large"
            closeAriaLabel="Close map selector"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={onDismiss}>Cancel</Button>
                        <Button onClick={clearShape}>Reset shape</Button>
                        <Button variant="primary" onClick={apply}>
                            Apply filter
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween direction="vertical" size="m">
                <SpaceBetween direction="horizontal" size="m">
                    <FormField label="Mode">
                        <SegmentedControl
                            selectedId={mode}
                            onChange={({ detail }) =>
                                handleModeChange(detail.selectedId as SelectorMode)
                            }
                            options={[
                                { id: "point", text: "Point + radius", iconName: "location-pin" },
                                { id: "bbox", text: "Bounding box", iconName: "expand" },
                                { id: "polygon", text: "Polygon", iconName: "edit" },
                            ]}
                        />
                    </FormField>
                    <FormField label="Relation">
                        <Select
                            selectedOption={
                                RELATION_OPTIONS.find((o) => o.value === relation) ||
                                RELATION_OPTIONS[0]
                            }
                            options={RELATION_OPTIONS}
                            onChange={({ detail }) =>
                                setRelation(
                                    (detail.selectedOption.value ||
                                        "intersects") as GeoSearchFilter["relation"]
                                )
                            }
                        />
                    </FormField>
                </SpaceBetween>

                {mode === "point" && (
                    <FormField
                        label="Radius (meters)"
                        description="Leave blank for a point-only filter; provide a value to filter within a circle around the point."
                    >
                        <Input
                            value={radius}
                            onChange={({ detail }) => setRadius(detail.value)}
                            placeholder="5000"
                            type="number"
                        />
                    </FormField>
                )}

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
                        attributionControl={false}
                        onClick={handleMapClick}
                        onLoad={() => setMapLoaded(true)}
                        cursor="crosshair"
                        interactive={true}
                        // Force mercator to avoid globe-projection easing
                        // warnings on maplibre-gl 5.x.
                        projection={{ type: "mercator" } as any}
                    >
                        <NavigationControl position="top-right" showZoom showCompass={false} />

                        {/* Overlay source/layers are managed imperatively via
                            the map ref in a useEffect (see SOURCE_ID below).
                            The declarative <Source>/<Layer> path was racing
                            the AWS Location Service style-load lifecycle and
                            silently failing to register layers, so we attach
                            them directly to the GL instance once it has fired
                            its 'load' event. */}

                        {/* Single point marker for the point mode */}
                        {mode === "point" && point && (
                            <Marker longitude={point[0]} latitude={point[1]} anchor="bottom">
                                <div style={{ width: "24px", height: "24px" }}>
                                    <svg viewBox="0 0 24 24" fill="#0972d3">
                                        <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
                                    </svg>
                                </div>
                            </Marker>
                        )}

                        {/* Bbox corner markers */}
                        {mode === "bbox" &&
                            corners.map((c, i) => (
                                <Marker
                                    key={`bbox-c-${i}`}
                                    longitude={c[0]}
                                    latitude={c[1]}
                                    anchor="center"
                                >
                                    <div
                                        style={{
                                            width: "12px",
                                            height: "12px",
                                            borderRadius: "50%",
                                            background: "#0972d3",
                                            border: "2px solid white",
                                            boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
                                        }}
                                    />
                                </Marker>
                            ))}

                        {/* Polygon vertex markers */}
                        {mode === "polygon" &&
                            polygon.map((v, i) => (
                                <Marker
                                    key={`poly-v-${i}`}
                                    longitude={v[0]}
                                    latitude={v[1]}
                                    anchor="center"
                                >
                                    <div
                                        style={{
                                            width: "10px",
                                            height: "10px",
                                            borderRadius: "50%",
                                            background: "#0972d3",
                                            border: "2px solid white",
                                            boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
                                        }}
                                    />
                                </Marker>
                            ))}
                    </Map>
                </div>

                {/* Mode-specific micro-toolbar. Background is left transparent so the bar
                    inherits the modal surface in both light and dark mode. */}
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
                    <Box variant="small" color="text-body-secondary">
                        {mode === "point" && "Click the map to set a point."}
                        {mode === "bbox" &&
                            (corners.length === 0
                                ? "Click two opposite corners to define a bounding box."
                                : corners.length === 1
                                ? "Click the opposite corner to complete the bounding box."
                                : "Bounding box ready. Click again to redraw.")}
                        {mode === "polygon" &&
                            (polygon.length < 3
                                ? `Click the map to add vertices (${polygon.length} added; need at least 3).`
                                : `${polygon.length} vertices added. Apply closes the shape.`)}
                    </Box>
                    {mode === "polygon" && (
                        <ButtonGroup
                            ariaLabel="Polygon actions"
                            variant="icon"
                            items={[
                                {
                                    type: "icon-button",
                                    id: "remove-vertex",
                                    iconName: "remove",
                                    text: "Remove last vertex",
                                    disabled: polygon.length === 0,
                                },
                            ]}
                            onItemClick={({ detail }) => {
                                if (detail.id === "remove-vertex") removeLastVertex();
                            }}
                        />
                    )}
                </div>

                {error && (
                    <Box color="text-status-error" fontSize="body-s">
                        {error}
                    </Box>
                )}
            </SpaceBetween>
        </Modal>
    );
};

export default GeoFilterMapSelector;
