/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Popover, Box, SpaceBetween, Icon } from "@cloudscape-design/components";

interface ValueHistoryTooltipProps {
    oldKey?: string;
    oldValue?: string;
    oldType?: string;
    schemaDefaultValue?: string;
    hasChanges?: boolean;
}

export const ValueHistoryTooltip: React.FC<ValueHistoryTooltipProps> = React.memo(
    ({ oldKey, oldValue, oldType, schemaDefaultValue, hasChanges = false }) => {
        // Show tooltip if there are changes (even if old value was empty) OR if there's a schema default
        const hasOldValue = hasChanges && oldValue !== undefined;
        const hasSchemaDefault = schemaDefaultValue && schemaDefaultValue.trim() !== "";

        if (!hasOldValue && !hasSchemaDefault) {
            return null;
        }

        return (
            <Popover
                dismissButton={false}
                position="top"
                size="medium"
                triggerType="custom"
                content={
                    <SpaceBetween direction="vertical" size="xs">
                        <Box variant="h4">Change Information</Box>

                        {hasChanges && oldKey && (
                            <div>
                                <strong>Previous Key:</strong>
                                <div
                                    style={{
                                        marginTop: "4px",
                                        padding: "8px",
                                        background: "#f5f5f5",
                                        borderRadius: "4px",
                                        fontFamily: "monospace",
                                        fontSize: "12px",
                                        maxWidth: "300px",
                                        wordBreak: "break-word",
                                    }}
                                >
                                    {oldKey}
                                </div>
                            </div>
                        )}

                        {hasChanges && oldType && (
                            <div>
                                <strong>Previous Type:</strong>
                                <div
                                    style={{
                                        marginTop: "4px",
                                        padding: "8px",
                                        background: "#f5f5f5",
                                        borderRadius: "4px",
                                        fontFamily: "monospace",
                                        fontSize: "12px",
                                        maxWidth: "300px",
                                        wordBreak: "break-word",
                                    }}
                                >
                                    {oldType}
                                </div>
                            </div>
                        )}

                        {hasOldValue && (
                            <div>
                                <strong>Previous Value:</strong>
                                <div
                                    style={{
                                        marginTop: "4px",
                                        padding: "8px",
                                        background: "#f5f5f5",
                                        borderRadius: "4px",
                                        fontFamily: "monospace",
                                        fontSize: "12px",
                                        maxWidth: "300px",
                                        wordBreak: "break-word",
                                    }}
                                >
                                    {oldValue || "(empty)"}
                                </div>
                            </div>
                        )}

                        {hasSchemaDefault && (
                            <div>
                                <strong>Schema Default:</strong>
                                <div
                                    style={{
                                        marginTop: "4px",
                                        padding: "8px",
                                        background: "#e8f4f8",
                                        borderRadius: "4px",
                                        fontFamily: "monospace",
                                        fontSize: "12px",
                                        maxWidth: "300px",
                                        wordBreak: "break-word",
                                    }}
                                >
                                    {schemaDefaultValue}
                                </div>
                            </div>
                        )}
                    </SpaceBetween>
                }
            >
                <Icon name="status-info" variant="subtle" />
            </Popover>
        );
    }
);

ValueHistoryTooltip.displayName = "ValueHistoryTooltip";

export default ValueHistoryTooltip;
