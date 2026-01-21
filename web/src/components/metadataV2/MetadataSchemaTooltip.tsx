/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Popover, Box, SpaceBetween, Icon } from "@cloudscape-design/components";

interface MetadataSchemaTooltipProps {
    schemaName?: string;
    required?: boolean;
    dependsOn?: string[];
    controlledListKeys?: string[];
    multiFieldConflict?: boolean;
    defaultValue?: string;
    sequence?: number;
}

export const MetadataSchemaTooltip: React.FC<MetadataSchemaTooltipProps> = React.memo(
    ({
        schemaName,
        required,
        dependsOn,
        controlledListKeys,
        multiFieldConflict,
        defaultValue,
        sequence,
    }) => {
        return (
            <Popover
                dismissButton={false}
                position="top"
                size="large"
                triggerType="custom"
                content={
                    <SpaceBetween direction="vertical" size="xs">
                        <Box variant="h4">Schema Information</Box>

                        {schemaName && (
                            <div>
                                <strong>Schema:</strong> {schemaName}
                            </div>
                        )}

                        {required !== undefined && (
                            <div>
                                <strong>Required:</strong> {required ? "Yes" : "No"}
                            </div>
                        )}

                        {sequence !== undefined && sequence !== null && (
                            <div>
                                <strong>Display Order:</strong> {sequence}
                            </div>
                        )}

                        {defaultValue && (
                            <div>
                                <strong>Default Value:</strong> {defaultValue}
                            </div>
                        )}

                        {dependsOn && dependsOn.length > 0 && (
                            <div>
                                <strong>Depends On:</strong>
                                <ul style={{ margin: "4px 0", paddingLeft: "20px" }}>
                                    {dependsOn.map((dep) => (
                                        <li key={dep}>{dep}</li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {controlledListKeys && controlledListKeys.length > 0 && (
                            <div>
                                <strong>Allowed Values:</strong>
                                <ul style={{ margin: "4px 0", paddingLeft: "20px" }}>
                                    {controlledListKeys.map((key) => (
                                        <li key={key}>{key}</li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {multiFieldConflict && (
                            <Box color="text-status-warning">
                                <Icon name="status-warning" /> Multiple schemas define this field
                                with different settings
                            </Box>
                        )}
                    </SpaceBetween>
                }
            >
                <Icon name="status-info" variant="link" />
            </Popover>
        );
    }
);

MetadataSchemaTooltip.displayName = "MetadataSchemaTooltip";

export default MetadataSchemaTooltip;
