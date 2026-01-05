/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Box, Container, Header } from "@cloudscape-design/components";

interface MetadataLoadingSkeletonProps {
    rowCount?: number;
}

export const MetadataLoadingSkeleton: React.FC<MetadataLoadingSkeletonProps> = ({
    rowCount = 5,
}) => {
    return (
        <Container
            header={
                <Header variant="h3">
                    <div
                        style={{
                            width: "120px",
                            height: "20px",
                            background:
                                "linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%)",
                            backgroundSize: "200% 100%",
                            animation: "shimmer 1.5s infinite",
                            borderRadius: "4px",
                        }}
                    />
                </Header>
            }
        >
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                    <tr>
                        <th
                            style={{
                                padding: "12px",
                                textAlign: "left",
                                borderBottom: "2px solid #e9ebed",
                                width: "25%",
                            }}
                        >
                            Metadata Key
                        </th>
                        <th
                            style={{
                                padding: "12px",
                                textAlign: "left",
                                borderBottom: "2px solid #e9ebed",
                                width: "20%",
                            }}
                        >
                            Value Type
                        </th>
                        <th
                            style={{
                                padding: "12px",
                                textAlign: "left",
                                borderBottom: "2px solid #e9ebed",
                                width: "40%",
                            }}
                        >
                            Metadata Value
                        </th>
                        <th
                            style={{
                                padding: "12px",
                                textAlign: "left",
                                borderBottom: "2px solid #e9ebed",
                                width: "15%",
                            }}
                        >
                            Actions
                        </th>
                    </tr>
                </thead>
                <tbody>
                    {Array.from({ length: rowCount }).map((_, index) => (
                        <tr key={index}>
                            <td style={{ padding: "12px", verticalAlign: "middle" }}>
                                <div
                                    style={{
                                        width: "80%",
                                        height: "16px",
                                        background:
                                            "linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%)",
                                        backgroundSize: "200% 100%",
                                        animation: "shimmer 1.5s infinite",
                                        borderRadius: "4px",
                                    }}
                                />
                            </td>
                            <td style={{ padding: "12px", verticalAlign: "middle" }}>
                                <div
                                    style={{
                                        width: "60%",
                                        height: "16px",
                                        background:
                                            "linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%)",
                                        backgroundSize: "200% 100%",
                                        animation: "shimmer 1.5s infinite",
                                        borderRadius: "4px",
                                    }}
                                />
                            </td>
                            <td style={{ padding: "12px", verticalAlign: "middle" }}>
                                <div
                                    style={{
                                        width: "90%",
                                        height: "16px",
                                        background:
                                            "linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%)",
                                        backgroundSize: "200% 100%",
                                        animation: "shimmer 1.5s infinite",
                                        borderRadius: "4px",
                                    }}
                                />
                            </td>
                            <td style={{ padding: "12px", verticalAlign: "middle" }}>
                                <div style={{ display: "flex", gap: "8px" }}>
                                    <div
                                        style={{
                                            width: "60px",
                                            height: "32px",
                                            background:
                                                "linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%)",
                                            backgroundSize: "200% 100%",
                                            animation: "shimmer 1.5s infinite",
                                            borderRadius: "4px",
                                        }}
                                    />
                                    <div
                                        style={{
                                            width: "60px",
                                            height: "32px",
                                            background:
                                                "linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%)",
                                            backgroundSize: "200% 100%",
                                            animation: "shimmer 1.5s infinite",
                                            borderRadius: "4px",
                                        }}
                                    />
                                </div>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>

            <style>{`
                @keyframes shimmer {
                    0% {
                        background-position: -200% 0;
                    }
                    100% {
                        background-position: 200% 0;
                    }
                }
            `}</style>
        </Container>
    );
};

export default MetadataLoadingSkeleton;
