import * as React from "react";
import SegmentedControl from "@cloudscape-design/components/segmented-control";

interface SearchPageSegmentedControlProps {
    onchange: (selectedId: string) => void;
    selectedId: string;
}
const SearchPageSegmentedControl = ({ onchange, selectedId }: SearchPageSegmentedControlProps) => {
    return (
        <SegmentedControl
            selectedId={selectedId}
            onChange={({ detail }) => {
                onchange(detail.selectedId);
            }}
            label="Select Map or List view"
            options={[
                { text: "Map View", id: "mapview" },
                { text: "List View", id: "listview" },
            ]}
        />
    );
};

export default SearchPageSegmentedControl;
