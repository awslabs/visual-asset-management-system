import * as React from "react";
import SegmentedControl from "@cloudscape-design/components/segmented-control";

interface SearchPageSegmentedControlProps {
    onchange: (selectedId: string) => void
}
export default ({onchange}: SearchPageSegmentedControlProps) => {
    const [selectedId, setSelectedId] = React.useState(
        "mapview"
    );
    return (
        <SegmentedControl
            selectedId={selectedId}
            onChange={({ detail }) => {
                setSelectedId(detail.selectedId)
                onchange(detail.selectedId)
            }}
            label="Select Map or List view"
            options={[
                { text: "Map View", id: "mapview" },
                { text: "List View", id: "listview" },
            ]}
        />
    );
}