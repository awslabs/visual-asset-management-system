import React, { useRef, useState, useEffect } from "react";
import { Link, MapView } from "@aws-amplify/ui-react";
import { SearchPageViewProps } from "./SearchPage";
import { Marker, Popup, MapRef } from "react-map-gl";
import Button from "@cloudscape-design/components/button";
import { LngLat, LngLatBounds, LngLatBoundsLike } from "maplibre-gl";
import { Box } from "@cloudscape-design/components";

function SearchPageMarker({ state, dispatch }: SearchPageViewProps) {
    return (
        <>
            {state.result?.hits?.hits
                ?.filter((hit: any) => {
                    return hit?._source?.gp_location?.lon && hit?._source?.gp_location?.lat;
                })
                .map((result: any) => {
                    console.log(
                        "marker result",
                        result._source.gp_location.lon,
                        result._source.gp_location.lat
                    );
                    return (
                        <Marker
                            key={result._id}
                            longitude={result._source.gp_location.lon}
                            latitude={result._source.gp_location.lat}
                            anchor="bottom"
                            onClick={(e) => {
                                // If we let the click event propagates to the map, it will immediately close the popup
                                // with `closeOnClick: true`
                                e.originalEvent.stopPropagation();
                                dispatch({
                                    type: "set-popup-info",
                                    payload: {
                                        latitude: result._source.gp_location.lat,
                                        longitude: result._source.gp_location.lon,
                                        databaseId: result._source.str_databaseid,
                                        assetId: result._source.str_assetid,
                                    },
                                });
                            }}
                        />
                    );
                })}
        </>
    );
}

const getMinMaxLatLongBounds = (result: any) => {
    let minLat = -90;
    let maxLat = 90;
    let minLong = -180;
    let maxLong = 180;
    result?.hits?.hits
        ?.filter((hit: any) => hit?._source?.gp_location?.lat && hit?._source?.gp_location?.lon)
        .forEach((hit: any) => {
            console.log("hit", hit?._source?.gp_location?.lat, hit?._source?.gp_location?.lon);
            minLat = Math.max(minLat, hit?._source?.gp_location?.lat);
            maxLat = Math.min(maxLat, hit?._source?.gp_location?.lat);
            minLong = Math.max(minLong, hit?._source?.gp_location?.lon);
            maxLong = Math.min(maxLong, hit?._source?.gp_location?.lon);
        });
    const r = {
        minLat,
        maxLat,
        minLong,
        maxLong,
    };
    console.log("bounds result", r);
    return r;
};

function SearchPageMapView({ state, dispatch }: SearchPageViewProps) {
    const mapRef = useRef<MapRef>({} as MapRef);

    return (
        <div>
            {state?.result?.hits?.total?.value ? (
                <>
                    <Box variant="h2">{state?.result?.hits?.total?.value} results</Box>
                </>
            ) : (
                <Box variant="h2">No search results</Box>
            )}
            <MapView
                ref={mapRef}
                style={{ width: "72vw" }}
                onLoad={(map) => {
                    dispatch({ type: "set-map", map });
                    const { minLat, maxLat, minLong, maxLong } = getMinMaxLatLongBounds(
                        state.result
                    );
                    map.target.fitBounds(
                        [
                            [minLong, minLat],
                            [maxLong, maxLat],
                        ],
                        { padding: 40, duration: 1000 }
                    );
                }}
                maxZoom={18}
                initialViewState={{
                    latitude: 37.8,
                    longitude: -122.4,
                    zoom: 14,
                }}
                //@ts-ignore
            >
                {/*<Marker longitude={-100} latitude={40} anchor="bottom" >*/}
                <SearchPageMarker state={state} dispatch={dispatch} />

                {state.popupInfo && (
                    <Popup
                        anchor="top"
                        longitude={Number(state.popupInfo.longitude)}
                        latitude={Number(state.popupInfo.latitude)}
                        onClose={() => dispatch({ type: "set-popup-info", payload: null })}
                    >
                        <div>
                            <Link
                                href={`#/databases/${state.popupInfo.databaseId}/assets/${state.popupInfo.assetId}`}
                            >
                                <Button variant="primary">View Asset</Button>
                            </Link>
                        </div>
                    </Popup>
                )}
            </MapView>
        </div>
    );
}

export default SearchPageMapView;
