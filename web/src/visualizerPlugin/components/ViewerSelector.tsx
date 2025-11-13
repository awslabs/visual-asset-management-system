/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Select, SelectProps } from "@cloudscape-design/components";
import { ViewerPlugin } from "../core/PluginRegistry";

interface ViewerSelectorProps {
    viewers: ViewerPlugin[];
    selectedViewerId: string | null;
    onViewerChange: (viewerId: string) => void;
    className?: string;
}

export const ViewerSelector: React.FC<ViewerSelectorProps> = ({
    viewers,
    selectedViewerId,
    onViewerChange,
    className,
}) => {
    // Convert viewers to options for Select component with enhanced descriptions
    const options: SelectProps.Option[] = viewers.map((viewer) => {
        const extensions = viewer.config.supportedExtensions.join(", ");
        const multiFileSupport = viewer.config.supportsMultiFile
            ? "Multi-File: Yes"
            : "Multi-File: No";
        const enhancedDescription = `${viewer.config.description} | ${multiFileSupport} | Extensions: ${extensions}`;

        return {
            label: viewer.config.name,
            value: viewer.config.id,
            description: enhancedDescription,
        };
    });

    // Find the selected option
    const selectedOption = options.find((option) => option.value === selectedViewerId) || null;

    // Determine if selection is required (multiple viewers available but none selected)
    const isSelectionRequired = viewers.length > 1 && !selectedViewerId;

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
        />
    );
};

export default ViewerSelector;
