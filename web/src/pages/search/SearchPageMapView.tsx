import React from 'react';
import {MapView} from "@aws-amplify/ui-react";

export interface SearchPageMapViewProps {

};

function SearchPageMapView(props: SearchPageMapViewProps) {
    return (

        <div>
            <MapView
                style={{width: "72vw"}}
                initialViewState={{
                    latitude: 37.8,
                    longitude: -122.4,
                    zoom: 14,
                }}/>
        </div>
    );
}

export default SearchPageMapView;