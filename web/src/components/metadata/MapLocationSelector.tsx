import Modal from "@cloudscape-design/components/modal";
import { createMap } from "maplibre-gl-js-amplify";
import "maplibre-gl/dist/maplibre-gl.css";

import { createAmplifyGeocoder } from "maplibre-gl-js-amplify";
import "@maplibre/maplibre-gl-geocoder/dist/maplibre-gl-geocoder.css";

import { useCallback, useEffect, useRef, useState } from "react";
import { Box, Button, Popover, StatusIndicator } from "@cloudscape-design/components";
import { LngLat, LngLatLike, Map } from "maplibre-gl";
import { Geo } from "aws-amplify";

import { Flex, Heading, MapView, View, Text, Image } from "@aws-amplify/ui-react";

import MapboxDraw from "@mapbox/mapbox-gl-draw";

import "@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css";
import { MapboxMap, useMap } from "react-map-gl";
import { FeatureCollection, Geometry } from "geojson";

async function initializeMap(
    elem: string,
    setValue: (l: [number, number], zoom: number) => void,
    init: LngLatLike | null,
    zoom: number
) {
    const map = await createMap({
        container: elem, // An HTML Element or HTML element ID to render the map in https://maplibre.org/maplibre-gl-js-docs/api/map/
        // center: [-123.1187, 49.2819], // [Longitude, Latitude]
        center: init ?? [-95.37019986475366, 29.767650706163337], // Houston
        zoom,
    });
    // map.on("click", function (e) {
    //     // The event object (e) contains information like the
    //     // coordinates of the point on the map that was clicked.
    //     // console.log("A click event has occurred at " + e.lngLat, e.point, e.target.getZoom());
    //     setValue(e.lngLat.toArray(), e.target.getZoom());
    // });
    map.on("moveend", function (e) {
        setValue(e.target.getCenter().toArray(), e.target.getZoom());
    });
    map.on("zoomend", function (e) {
        setValue(e.target.getCenter().toArray(), e.target.getZoom());
    });

    // map.addControl(createAmplifyGeocoder());
    // console.log("available maps", Geo.getAvailableMaps());

    // const searchOptionsWithSearchAreaConstraints = {
    // countries: ["USA"], // Alpha-3 country codes
    // maxResults: 25, // 50 is the max and the default
    // searchIndexName: string, // the string name of the search index
    //   }

    //   Geo.searchByText('Amazon Go Stores', searchOptionsWithBiasPosition)
}

// initializeMap();

interface MapLocationSelectorModalProps {
    location: [number, number] | null;
    setLocation: (loc: [number, number], zoom: number) => void;
    initialZoom: number;
    disabled: boolean;
}

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
                    })
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
        [input, setJson]
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
                            })
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
                            })
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

export default function MapLocationSelectorModal({
    location,
    setLocation,
    initialZoom,
    disabled,
}: MapLocationSelectorModalProps) {
    const [open, setOpen] = useState(false);

    const location1 = LngLat.convert(location ?? [-95.37019986475366, 29.767650706163337]);

    useEffect(() => {
        console.log("map state", open);
        if (!open) return;

        console.log("initmap");
        initializeMap("themap", setLocation, location1, initialZoom).then((x) => {
            console.log("map initialized", location, initialZoom);
        });
    }, [open]);

    const ll = (location && LngLat.convert(location1)) || null;

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
                <div id="themap" style={{ width: "54vw", height: "80vh" }}>
                    Loading Map...
                </div>
                <LocationDetailCopy ll={ll} initialZoom={initialZoom} />
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
                                [ll?.lat, ll?.lng, initialZoom].join("\t")
                            );
                        }}
                    />
                </Popover>
            </Box>
        </>
    );
}
