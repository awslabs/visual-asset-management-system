/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import FileMetadata from "./FileMetadata";

// Mock ControlledMetadata component
jest.mock("./ControlledMetadata", () => {
    return function MockControlledMetadata({ databaseId, assetId, prefix }: any) {
        return (
            <div data-testid="controlled-metadata">
                Mock ControlledMetadata - {databaseId}/{assetId}/{prefix}
            </div>
        );
    };
});

describe("FileMetadata", () => {
    const defaultProps = {
        databaseId: "test-db",
        assetId: "test-asset",
        prefix: "test-prefix",
    };

    it("renders with default props", () => {
        render(<FileMetadata {...defaultProps} />);

        expect(screen.getByTestId("file-metadata")).toBeInTheDocument();
    });

    it("shows header when showHeader is true", () => {
        render(<FileMetadata {...defaultProps} showHeader={true} />);

        expect(screen.getByText("Metadata")).toBeInTheDocument();
    });

    it("does not show header when showHeader is false", () => {
        render(<FileMetadata {...defaultProps} showHeader={false} />);

        expect(screen.queryByText("Metadata")).not.toBeInTheDocument();
    });

    it("applies custom className", () => {
        render(<FileMetadata {...defaultProps} className="custom-class" />);

        const container = screen.getByTestId("file-metadata");
        expect(container).toHaveClass("custom-class");
    });

    it("passes correct props to ControlledMetadata", async () => {
        render(<FileMetadata {...defaultProps} />);

        // Wait for loading to complete and ControlledMetadata to render
        await screen.findByTestId("controlled-metadata");

        expect(
            screen.getByText("Mock ControlledMetadata - test-db/test-asset/test-prefix")
        ).toBeInTheDocument();
    });

    it("shows validation errors when showErrors is true", () => {
        render(<FileMetadata {...defaultProps} showErrors={true} />);

        expect(screen.getByTestId("file-metadata")).toBeInTheDocument();
    });

    it("hides validation errors when showErrors is false", () => {
        render(<FileMetadata {...defaultProps} showErrors={false} />);

        expect(screen.getByTestId("file-metadata")).toBeInTheDocument();
    });
});
