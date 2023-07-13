/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import Modal from "@cloudscape-design/components/modal";
import "maplibre-gl/dist/maplibre-gl.css";

import "@maplibre/maplibre-gl-geocoder/dist/maplibre-gl-geocoder.css";

import { useCallback, useState } from "react";
import { Box, Button, Popover, StatusIndicator } from "@cloudscape-design/components";
import { LngLat } from "maplibre-gl";

import { MapView } from "@aws-amplify/ui-react";

import MapboxDraw from "@mapbox/mapbox-gl-draw";

import "@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css";
import { FeatureCollection } from "geojson";

interface MapLocationSelectorModalProps2 {
    json: string;
    setJson: (json: string) => void;
    disabled: boolean;
}

interface MapValue {
    loc: [number, number];
    zoom: number;
    polygons: FeatureCollection;
}

export function MapLocationSelectorModal2({
    json,
    setJson,
    disabled,
}: MapLocationSelectorModalProps2) {
    const [open, setOpen] = useState(false);
    const input: MapValue = JSON.parse(json);
    const ll = (input.loc && LngLat.convert(input.loc)) || null;

    const onMapLoad = useCallback(
        ({ target: map }) => {
            if (!map) {
                console.log("map undefined");
                return;
            }
            var draw = new MapboxDraw({
                displayControlsDefault: false,
                controls: {
                    polygon: true,
                    trash: true,
                },
            });
            map.addControl(draw);
            console.log("loaded controls", draw);
            console.log("draw", draw);

            function updateArea(e: { type: string }) {
                console.log("polygon update", e);
                setJson(
                    JSON.stringify({
                        ...input,
                        polygons: draw.getAll(),
                    }),
                );
            }
            map.on("draw.create", updateArea);
            map.on("draw.delete", updateArea);
            map.on("draw.update", updateArea);

            if (input.polygons) {
                try {
                    console.log("set polygons", input.polygons);
                    draw.set(input.polygons);
                } catch (e) {
                    console.log("error setting polygons", e);
                }
            }
        },
        [input, setJson],
    );

    return (
        (open && (
            <Modal
                visible={open}
                size="large"
                onDismiss={() => {
                    setOpen(false);
                }}
                header="Select a Location"
            >
                <MapView
                    initialViewState={{
                        longitude: ll?.lng,
                        latitude: ll?.lat,
                        zoom: input.zoom,
                    }}
                    maxZoom={18}
                    minZoom={5}
                    onZoomEnd={(e) => {
                        type C = [number, number];
                        setJson(
                            JSON.stringify({
                                ...input,
                                loc: e.target.getCenter().toArray() as C,
                                zoom: e.target.getZoom(),
                            }),
                        );
                    }}
                    onLoad={onMapLoad}
                    onDragEnd={(e) => {
                        type C = [number, number];
                        setJson(
                            JSON.stringify({
                                ...input,
                                loc: e.target.getCenter().toArray() as C,
                                zoom: e.target.getZoom(),
                            }),
                        );
                    }}
                    style={{ width: "54vw", height: "80vh" }}
                />
                <LocationDetailCopy ll={ll} initialZoom={input.zoom} />
            </Modal>
        )) || (
            <>
                {ll && `${ll.lat}, ${ll.lng}`}
                <br />
                <Button disabled={disabled} onClick={() => setOpen(true)}>
                    Open Map
                </Button>
            </>
        )
    );
}

function LocationDetailCopy({ ll, initialZoom }: any) {
    return (
        <>
            Current center point (lat, long, zoom): {[ll?.lat, ll?.lng, initialZoom].join("\t")}
            <Box margin={{ right: "xxs" }} display="inline-block">
                <Popover
                    size="small"
                    position="top"
                    triggerType="custom"
                    dismissButton={false}
                    content={<StatusIndicator type="success">Coordinates copied</StatusIndicator>}
                >
                    <Button
                        variant="inline-icon"
                        iconName="copy"
                        onClick={() => {
                            /* copy to clipboard implementation */
                            navigator.clipboard.writeText(
                                [ll?.lat, ll?.lng, initialZoom].join("\t"),
                            );
                        }}
                    />
                </Popover>
            </Box>
        </>
    );
}
