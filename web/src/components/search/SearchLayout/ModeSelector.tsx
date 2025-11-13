/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import {
    FormField,
    SegmentedControl,
    Box,
    Toggle,
    SpaceBetween,
} from "@cloudscape-design/components";
import Synonyms from "../../../synonyms";

interface ModeSelectorProps {
    recordType: "asset" | "file";
    onRecordTypeChange: (type: "asset" | "file") => void;
    showThumbnails: boolean;
    onThumbnailToggle: () => void;
    disabled?: boolean;
}

const ModeSelector: React.FC<ModeSelectorProps> = ({
    recordType,
    onRecordTypeChange,
    showThumbnails,
    onThumbnailToggle,
    disabled = false,
}) => {
    return (
        <Box>
            <FormField
                label={
                    <Box fontSize="heading-s" fontWeight="bold">
                        Search Mode
                    </Box>
                }
                description="Select what type of records to search"
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
                </SpaceBetween>
            </FormField>
        </Box>
    );
};

export default ModeSelector;
