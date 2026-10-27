/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { CreateFolderModal } from "./CreateFolderModal";

const props = {
    visible: true,
    onDismiss: jest.fn(),
    onSubmit: jest.fn(),
    parentFolder: "optimized",
    isLoading: false,
};

const typeName = (value: string) => {
    const input = createWrapper(document.body).findModal()!.findContent().findInput()!;
    input.setInputValue(value);
};

describe("CreateFolderModal request error", () => {
    it("shows the error, hides it once the name changes, and shows the next one", () => {
        const { rerender } = render(<CreateFolderModal {...props} submitError="" />);
        expect(screen.queryByText("Failed to create folder: Forbidden")).toBeNull();

        rerender(<CreateFolderModal {...props} submitError="Failed to create folder: Forbidden" />);
        expect(screen.getByText("Failed to create folder: Forbidden")).toBeInTheDocument();

        typeName("renamed");
        expect(screen.queryByText("Failed to create folder: Forbidden")).toBeNull();

        // The panel clears the error when a submit starts, so a repeat of the same failure is
        // a new value.
        rerender(<CreateFolderModal {...props} submitError="" />);
        rerender(<CreateFolderModal {...props} submitError="Failed to create folder: Forbidden" />);
        expect(screen.getByText("Failed to create folder: Forbidden")).toBeInTheDocument();
    });

    it("shows its own validation error ahead of the request error", () => {
        render(<CreateFolderModal {...props} submitError="Failed to create folder: Forbidden" />);
        typeName("bad/name");
        fireEvent.click(screen.getByRole("button", { name: "Create" }));
        expect(
            screen.getAllByText(/Folder name contains invalid characters/).length
        ).toBeGreaterThan(0);
        expect(screen.queryByText("Failed to create folder: Forbidden")).toBeNull();
    });
});
