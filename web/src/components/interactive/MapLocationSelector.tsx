import Modal from "@cloudscape-design/components/modal";
import { createMap } from "maplibre-gl-js-amplify";
import "maplibre-gl/dist/maplibre-gl.css";

import { createAmplifyGeocoder } from "maplibre-gl-js-amplify";
import "@maplibre/maplibre-gl-geocoder/dist/maplibre-gl-geocoder.css";

import { useEffect, useRef, useState } from "react";
import { Button } from "@cloudscape-design/components";
import { LngLat, LngLatLike } from "maplibre-gl";
import { Geo } from "aws-amplify";

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
}

export default function MapLocationSelectorModal({
    location,
    setLocation,
    initialZoom,
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
                Selected Location: {ll && `${ll.lng}, ${ll.lat}, ${initialZoom}`}
            </Modal>
        )) || (
            <>
                {ll && `${ll.lng}, ${ll.lat}`}
                <Button onClick={() => setOpen(true)}>Open Map</Button>
            </>
        )
    );
}
