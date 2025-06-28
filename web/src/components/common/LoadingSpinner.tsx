/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Box, Spinner } from "@cloudscape-design/components";

interface LoadingSpinnerProps {
    size?: "normal" | "big" | "large";
    text?: string;
    centered?: boolean;
    fullHeight?: boolean;
}

export const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({
    size = "normal",
    text = "Loading...",
    centered = true,
    fullHeight = false,
}) => {
    const containerStyle: React.CSSProperties = {
        display: "flex",
        flexDirection: "column",
        alignItems: centered ? "center" : "flex-start",
        justifyContent: centered ? "center" : "flex-start",
        padding: "20px",
        height: fullHeight ? "100%" : "auto",
        minHeight: fullHeight ? "200px" : "auto",
    };

    return (
        <div style={containerStyle}>
            <Spinner size={size} />
            {text && <Box padding={{ top: "s" }}>{text}</Box>}
        </div>
    );
};

export default LoadingSpinner;
