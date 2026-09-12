/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Select, { SelectProps } from "@cloudscape-design/components/select";
import { ViewerPlugin } from "../core/PluginRegistry";
import { ViewerMode } from "../core/PluginRegistry";
import { isCompareOnlyViewer } from "../core/viewerSelection";

interface ViewerSelectorProps {
    viewers: ViewerPlugin[];
    selectedViewerId: string | null;
    onViewerChange: (viewerId: string) => void;
    className?: string;
    /** When "compare", option descriptions report the compare-file window instead of multi-file. */
    mode?: ViewerMode;
}

/**
 * The viewers this selector may list for a mode. The registry already excludes compare-only viewers
 * from the visualize path (`admitsVisualizeSelection`); this is the same rule applied at the control,
 * so a caller that builds its own list — or a stale one — can never put a differ in the Visualize
 * dropdown for ANY file count. A compare-only viewer reads `compareFiles`, which visualize never
 * passes, so listing it would only ever open an immediate "needs N files" error.
 */
export function listableViewers(viewers: ViewerPlugin[], mode: ViewerMode): ViewerPlugin[] {
    if (mode === "compare") {
        return viewers;
    }
    return viewers.filter((viewer) => !isCompareOnlyViewer(viewer.config));
}

export const ViewerSelector: React.FC<ViewerSelectorProps> = ({
    viewers,
    selectedViewerId,
    onViewerChange,
    className,
    mode = "visualize",
}) => {
    // Convert viewers to options for Select component with enhanced descriptions
    const options: SelectProps.Option[] = listableViewers(viewers, mode).map((viewer) => {
        const extensions = viewer.config.supportedExtensions.join(", ");
        let capabilityText: string;
        if (mode === "compare" && viewer.config.compareMode) {
            const { minFiles, maxFiles } = viewer.config.compareMode;
            capabilityText = `Compare: ${minFiles}\u2013${maxFiles} files`;
        } else {
            capabilityText = viewer.config.supportsMultiFile ? "Multi-File: Yes" : "Multi-File: No";
        }
        const enhancedDescription = `${viewer.config.description} | ${capabilityText} | Extensions: ${extensions}`;

        return {
            label: viewer.config.name,
            value: viewer.config.id,
            description: enhancedDescription,
        };
    });

    // Find the selected option
    const selectedOption = options.find((option) => option.value === selectedViewerId) || null;

    // Determine if selection is required (multiple listable viewers but none selected)
    const isSelectionRequired = options.length > 1 && !selectedViewerId;

    const handleChange = (event: any) => {
        const selectedValue = event.detail.selectedOption?.value;
        if (selectedValue) {
            onViewerChange(selectedValue);
        }
    };

    if (options.length === 0) {
        return null; // Don't show selector if there are no viewers
    }

    return (
        <Select
            selectedOption={selectedOption}
            onChange={handleChange}
            options={options}
            placeholder={isSelectionRequired ? "Select viewer (required)" : "Select viewer"}
            className={className}
            triggerVariant="option"
            invalid={isSelectionRequired}
            controlId="viewer-selector"
            ariaLabel="Select viewer"
        />
    );
};

export default ViewerSelector;
