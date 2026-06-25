/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import {
    Box,
    Button,
    ExpandableSection,
    FormField,
    Grid,
    Input,
    Select,
    SpaceBetween,
    Textarea,
} from "@cloudscape-design/components";
import { GeoSearchFilter, SearchFilters } from "../types";
import { appCache } from "../../../services/appCache";
import { featuresEnabled } from "../../../common/constants/featuresEnabled";
import GeoFilterMapSelector from "./GeoFilterMapSelector";

interface GeoFilterPanelProps {
    filters: SearchFilters;
    onFilterChange: (key: string, value: any) => void;
    disabled?: boolean;
}

type GeoMode = "point" | "bbox" | "geojson";

const RELATION_OPTIONS = [
    { label: "Intersects", value: "intersects" },
    { label: "Within", value: "within" },
    { label: "Contains", value: "contains" },
    { label: "Disjoint", value: "disjoint" },
];

const GeoFilterPanel: React.FC<GeoFilterPanelProps> = ({
    filters,
    onFilterChange,
    disabled = false,
}) => {
    const existing = filters.geo_filter || null;
    const initialMode: GeoMode = existing?.bbox ? "bbox" : existing?.geoJson ? "geojson" : "point";

    const [mode, setMode] = useState<GeoMode>(initialMode);
    const [relation, setRelation] = useState<string>(existing?.relation || "intersects");

    const [pointLat, setPointLat] = useState<string>(
        existing?.point?.lat != null ? String(existing.point.lat) : ""
    );
    const [pointLon, setPointLon] = useState<string>(
        existing?.point?.lon != null ? String(existing.point.lon) : ""
    );
    const [pointRadius, setPointRadius] = useState<string>(
        existing?.point?.radiusMeters != null ? String(existing.point.radiusMeters) : ""
    );

    const [bboxTopLat, setBboxTopLat] = useState<string>(
        existing?.bbox?.topLeft?.lat != null ? String(existing.bbox.topLeft.lat) : ""
    );
    const [bboxTopLon, setBboxTopLon] = useState<string>(
        existing?.bbox?.topLeft?.lon != null ? String(existing.bbox.topLeft.lon) : ""
    );
    const [bboxBotLat, setBboxBotLat] = useState<string>(
        existing?.bbox?.bottomRight?.lat != null ? String(existing.bbox.bottomRight.lat) : ""
    );
    const [bboxBotLon, setBboxBotLon] = useState<string>(
        existing?.bbox?.bottomRight?.lon != null ? String(existing.bbox.bottomRight.lon) : ""
    );

    const [geoJsonText, setGeoJsonText] = useState<string>(
        existing?.geoJson ? JSON.stringify(existing.geoJson, null, 2) : ""
    );
    const [error, setError] = useState<string | null>(null);
    const [isMapSelectorOpen, setIsMapSelectorOpen] = useState<boolean>(false);

    // Map selector availability — gated on Location Services feature + a configured map style URL.
    const config = appCache.getItem("config");
    const mapSelectorAvailable =
        !!config?.featuresEnabled?.includes(featuresEnabled.LOCATIONSERVICES) &&
        !!config?.locationServiceApiUrl;
    const mapStyleUrl: string | undefined = config?.locationServiceApiUrl;

    // Reset error when the user changes any input.
    useEffect(
        () => setError(null),
        [
            mode,
            pointLat,
            pointLon,
            pointRadius,
            bboxTopLat,
            bboxTopLon,
            bboxBotLat,
            bboxBotLon,
            geoJsonText,
            relation,
        ]
    );

    const apply = () => {
        const result: GeoSearchFilter = { relation: relation as GeoSearchFilter["relation"] };
        if (mode === "point") {
            const lat = parseFloat(pointLat);
            const lon = parseFloat(pointLon);
            if (Number.isNaN(lat) || Number.isNaN(lon)) {
                setError("Latitude and longitude are required.");
                return;
            }
            result.point = { lat, lon };
            if (pointRadius.trim() !== "") {
                const radius = parseFloat(pointRadius);
                if (Number.isNaN(radius) || radius <= 0) {
                    setError("Radius must be a positive number of meters.");
                    return;
                }
                result.point.radiusMeters = radius;
            }
        } else if (mode === "bbox") {
            const tlLat = parseFloat(bboxTopLat);
            const tlLon = parseFloat(bboxTopLon);
            const brLat = parseFloat(bboxBotLat);
            const brLon = parseFloat(bboxBotLon);
            if ([tlLat, tlLon, brLat, brLon].some((n) => Number.isNaN(n))) {
                setError("All four bounding box corners are required.");
                return;
            }
            result.bbox = {
                topLeft: { lat: tlLat, lon: tlLon },
                bottomRight: { lat: brLat, lon: brLon },
            };
        } else {
            try {
                const parsed = JSON.parse(geoJsonText);
                if (!parsed || typeof parsed !== "object" || !parsed.type) {
                    setError("GeoJSON must be a Geometry, Feature, or FeatureCollection.");
                    return;
                }
                result.geoJson = parsed;
            } catch (e: any) {
                setError(`Invalid GeoJSON: ${e?.message || "parse error"}`);
                return;
            }
        }
        onFilterChange("geo_filter", result);
    };

    const clear = () => {
        setPointLat("");
        setPointLon("");
        setPointRadius("");
        setBboxTopLat("");
        setBboxTopLon("");
        setBboxBotLat("");
        setBboxBotLon("");
        setGeoJsonText("");
        setError(null);
        onFilterChange("geo_filter", null);
    };

    const summary = existing
        ? existing.point
            ? `Point ${existing.point.lat}, ${existing.point.lon}${
                  existing.point.radiusMeters ? ` (${existing.point.radiusMeters}m)` : ""
              }`
            : existing.bbox
            ? `Box ${existing.bbox.topLeft.lat},${existing.bbox.topLeft.lon} → ${existing.bbox.bottomRight.lat},${existing.bbox.bottomRight.lon}`
            : "GeoJSON shape"
        : null;

    return (
        <ExpandableSection
            headerText="Geospatial filter"
            variant="footer"
            defaultExpanded={!!existing}
        >
            <SpaceBetween direction="vertical" size="s">
                <Box variant="small" color="text-body-secondary">
                    {summary || "Filter by location (point, bounding box, or GeoJSON)"}
                </Box>
                <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                    <FormField label="Mode">
                        <Select
                            disabled={disabled}
                            selectedOption={
                                mode === "point"
                                    ? { label: "Point + radius", value: "point" }
                                    : mode === "bbox"
                                    ? { label: "Bounding box", value: "bbox" }
                                    : { label: "GeoJSON", value: "geojson" }
                            }
                            options={[
                                { label: "Point + radius", value: "point" },
                                { label: "Bounding box", value: "bbox" },
                                { label: "GeoJSON", value: "geojson" },
                            ]}
                            onChange={({ detail }) =>
                                setMode((detail.selectedOption.value as GeoMode) || "point")
                            }
                        />
                    </FormField>
                    <FormField label="Relation">
                        <Select
                            disabled={disabled}
                            selectedOption={
                                RELATION_OPTIONS.find((o) => o.value === relation) ||
                                RELATION_OPTIONS[0]
                            }
                            options={RELATION_OPTIONS}
                            onChange={({ detail }) =>
                                setRelation(detail.selectedOption.value || "intersects")
                            }
                        />
                    </FormField>
                </Grid>

                {mode === "point" && (
                    <Grid gridDefinition={[{ colspan: 4 }, { colspan: 4 }, { colspan: 4 }]}>
                        <FormField label="Latitude">
                            <Input
                                disabled={disabled}
                                value={pointLat}
                                onChange={({ detail }) => setPointLat(detail.value)}
                                placeholder="47.6062"
                            />
                        </FormField>
                        <FormField label="Longitude">
                            <Input
                                disabled={disabled}
                                value={pointLon}
                                onChange={({ detail }) => setPointLon(detail.value)}
                                placeholder="-122.3321"
                            />
                        </FormField>
                        <FormField label="Radius (meters)">
                            <Input
                                disabled={disabled}
                                value={pointRadius}
                                onChange={({ detail }) => setPointRadius(detail.value)}
                                placeholder="5000"
                            />
                        </FormField>
                    </Grid>
                )}

                {mode === "bbox" && (
                    <SpaceBetween direction="vertical" size="xs">
                        <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                            <FormField label="Top-left latitude">
                                <Input
                                    disabled={disabled}
                                    value={bboxTopLat}
                                    onChange={({ detail }) => setBboxTopLat(detail.value)}
                                />
                            </FormField>
                            <FormField label="Top-left longitude">
                                <Input
                                    disabled={disabled}
                                    value={bboxTopLon}
                                    onChange={({ detail }) => setBboxTopLon(detail.value)}
                                />
                            </FormField>
                        </Grid>
                        <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                            <FormField label="Bottom-right latitude">
                                <Input
                                    disabled={disabled}
                                    value={bboxBotLat}
                                    onChange={({ detail }) => setBboxBotLat(detail.value)}
                                />
                            </FormField>
                            <FormField label="Bottom-right longitude">
                                <Input
                                    disabled={disabled}
                                    value={bboxBotLon}
                                    onChange={({ detail }) => setBboxBotLon(detail.value)}
                                />
                            </FormField>
                        </Grid>
                    </SpaceBetween>
                )}

                {mode === "geojson" && (
                    <FormField
                        label="GeoJSON"
                        description="Paste a GeoJSON Geometry, Feature, or FeatureCollection."
                    >
                        <Textarea
                            disabled={disabled}
                            value={geoJsonText}
                            onChange={({ detail }) => setGeoJsonText(detail.value)}
                            rows={6}
                            placeholder='{"type":"Polygon","coordinates":[[[lon,lat],[lon,lat],...]]}'
                        />
                    </FormField>
                )}

                {error && (
                    <Box color="text-status-error" fontSize="body-s">
                        {error}
                    </Box>
                )}

                <SpaceBetween direction="horizontal" size="xs">
                    <Button onClick={apply} disabled={disabled} variant="primary">
                        Apply
                    </Button>
                    {mapSelectorAvailable && (
                        <Button
                            onClick={() => setIsMapSelectorOpen(true)}
                            disabled={disabled}
                            iconName="map"
                        >
                            Map selector
                        </Button>
                    )}
                    <Button onClick={clear} disabled={disabled || !existing}>
                        Clear
                    </Button>
                </SpaceBetween>
            </SpaceBetween>

            {mapSelectorAvailable && mapStyleUrl && (
                <GeoFilterMapSelector
                    visible={isMapSelectorOpen}
                    onDismiss={() => setIsMapSelectorOpen(false)}
                    initialFilter={existing}
                    mapStyleUrl={mapStyleUrl}
                    onApply={(filter) => {
                        // Push the filter through the same channel as the manual Apply
                        // button so the search request body picks it up unchanged.
                        onFilterChange("geo_filter", filter);
                        // Re-seed the local panel inputs from the modal's result so the
                        // textual fields match the map-selected shape.
                        setRelation(filter.relation || "intersects");
                        if (filter.point) {
                            setMode("point");
                            setPointLat(String(filter.point.lat));
                            setPointLon(String(filter.point.lon));
                            setPointRadius(
                                filter.point.radiusMeters != null
                                    ? String(filter.point.radiusMeters)
                                    : ""
                            );
                            setBboxTopLat("");
                            setBboxTopLon("");
                            setBboxBotLat("");
                            setBboxBotLon("");
                            setGeoJsonText("");
                        } else if (filter.bbox) {
                            setMode("bbox");
                            setBboxTopLat(String(filter.bbox.topLeft.lat));
                            setBboxTopLon(String(filter.bbox.topLeft.lon));
                            setBboxBotLat(String(filter.bbox.bottomRight.lat));
                            setBboxBotLon(String(filter.bbox.bottomRight.lon));
                            setPointLat("");
                            setPointLon("");
                            setPointRadius("");
                            setGeoJsonText("");
                        } else if (filter.geoJson) {
                            setMode("geojson");
                            setGeoJsonText(JSON.stringify(filter.geoJson, null, 2));
                            setPointLat("");
                            setPointLon("");
                            setPointRadius("");
                            setBboxTopLat("");
                            setBboxTopLon("");
                            setBboxBotLat("");
                            setBboxBotLon("");
                        }
                        setIsMapSelectorOpen(false);
                    }}
                />
            )}
        </ExpandableSection>
    );
};

export default GeoFilterPanel;
