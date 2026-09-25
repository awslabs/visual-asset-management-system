/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import FormField from "@cloudscape-design/components/form-field";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import Box from "@cloudscape-design/components/box";
import Toggle from "@cloudscape-design/components/toggle";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Synonyms from "../../../synonyms";

interface ModeSelectorProps {
    recordType: "asset" | "file";
    onRecordTypeChange: (type: "asset" | "file") => void;
    showThumbnails: boolean;
    onThumbnailToggle: () => void;
    showMapThumbnails?: boolean;
    onMapThumbnailToggle?: () => void;
    useMapView?: boolean;
    disabled?: boolean;
}

const ModeSelector: React.FC<ModeSelectorProps> = ({
    recordType,
    onRecordTypeChange,
    showThumbnails,
    onThumbnailToggle,
    showMapThumbnails = false,
    onMapThumbnailToggle,
    useMapView = false,
    disabled = false,
}) => {
    return (
        <Box>
            <FormField
                label={
                    <Box fontSize="heading-s" fontWeight="bold">
                        Search for
                    </Box>
                }
                description={`Return matching ${Synonyms.assets}, or the individual files inside them`}
            >
                <SpaceBetween direction="vertical" size="m">
                    <fieldset disabled={disabled} style={{ border: "none", padding: 0, margin: 0 }}>
                        <SegmentedControl
                            selectedId={recordType}
                            onChange={({ detail }) =>
                                onRecordTypeChange(detail.selectedId as "asset" | "file")
                            }
                            options={[
                                {
                                    text: Synonyms.Assets,
                                    id: "asset",
                                    iconName: "folder",
                                },
                                {
                                    text: "Files",
                                    id: "file",
                                    iconName: "file",
                                },
                            ]}
                        />
                    </fieldset>

                    <Toggle
                        onChange={onThumbnailToggle}
                        checked={showThumbnails}
                        disabled={disabled}
                    >
                        Show preview thumbnails
                    </Toggle>

                    {/* Show map thumbnail toggle when maps are enabled (assets and files) */}
                    {useMapView && onMapThumbnailToggle && (
                        <Toggle
                            onChange={onMapThumbnailToggle}
                            checked={showMapThumbnails}
                            disabled={disabled}
                        >
                            Show map thumbnails
                        </Toggle>
                    )}
                </SpaceBetween>
            </FormField>
        </Box>
    );
};

export default ModeSelector;
