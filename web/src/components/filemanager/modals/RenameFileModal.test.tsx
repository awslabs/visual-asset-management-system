/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Renaming a file does not require the new name to carry an extension.
 *
 * VAMS accepts a file whose name has no extension — `LICENSE`, `Dockerfile`, `Makefile`, and every
 * extension-less data export — through upload and through bucket-sync ingestion, and indexes it. This
 * modal rejected any name without a dot, and separately rejected a name that was ONLY an extension
 * (`.gitignore`, because the dot sits at index 0), so a file VAMS had already accepted could not be
 * renamed and no valid name could be typed for it. The message named a rule the system does not have.
 *
 * Asserted through the rendered modal rather than by exporting the validator, because what matters is
 * that the rename REQUEST is issued: a validator returning `null` while the submit path still refused
 * would look identical from the outside.
 *
 * The path-separator arm is not cosmetic. `handleRename` builds the destination by concatenating the
 * typed name onto the file's directory, so a `/` in the name silently MOVES the file into another
 * folder under the guise of a rename.
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { RenameFileModal } from "./RenameFileModal";

jest.mock("../../../services/FileOperationsService", () => ({
    moveFile: jest.fn(),
}));

const { moveFile } = require("../../../services/FileOperationsService");

function renderModal(fileName: string, relativePath: string) {
    const onSuccess = jest.fn();
    render(
        <RenameFileModal
            visible={true}
            onDismiss={jest.fn()}
            selectedFile={
                {
                    name: fileName,
                    relativePath,
                    isFolder: false,
                } as any
            }
            databaseId="db1"
            assetId="asset1"
            onSuccess={onSuccess}
        />
    );
    return { onSuccess };
}

function typeNameAndSubmit(newName: string) {
    const input = screen.getByPlaceholderText("Enter new filename");
    fireEvent.change(input, { target: { value: newName } });
    fireEvent.click(screen.getByRole("button", { name: /^Rename$/ }));
}

describe("RenameFileModal filename validation", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        moveFile.mockResolvedValue({ success: true });
    });

    it.each([
        ["an extension-less name", "README.md", "/README.md", "LICENSE"],
        ["a dotfile whose whole name is an extension", "config.txt", "/config.txt", ".gitignore"],
        ["renaming an extension-less file to another", "LICENSE", "/LICENSE", "NOTICE"],
        ["a name with an extension, unchanged behaviour", "a.txt", "/a.txt", "b.txt"],
    ])("accepts %s", async (_label, currentName, currentPath, newName) => {
        renderModal(currentName, currentPath);
        typeNameAndSubmit(newName);

        await waitFor(() => expect(moveFile).toHaveBeenCalledTimes(1));
        expect(moveFile).toHaveBeenCalledWith("db1", "asset1", {
            sourcePath: currentPath,
            destinationPath: `/${newName}`,
        });
        // And no error surfaced. `queryByText` so this reads as an absence rather than throwing.
        expect(screen.queryByText(/must have an extension/i)).toBeNull();
    });

    it("preserves the directory when renaming a file in a subfolder", async () => {
        renderModal("old.txt", "/sub/dir/old.txt");
        typeNameAndSubmit("LICENSE");

        await waitFor(() => expect(moveFile).toHaveBeenCalledTimes(1));
        expect(moveFile).toHaveBeenCalledWith("db1", "asset1", {
            sourcePath: "/sub/dir/old.txt",
            destinationPath: "/sub/dir/LICENSE",
        });
    });

    it.each([
        ["a blank name", "   ", /cannot be blank/i],
        ["a forward slash, which would move the file", "sub/LICENSE", /path separator/i],
        ["a backslash", "sub\\LICENSE", /path separator/i],
        ["a bare dot", ".", /cannot be '\.' or '\.\.'/i],
        ["a double dot", "..", /cannot be '\.' or '\.\.'/i],
    ])("rejects %s without calling the API", async (_label, newName, expected) => {
        renderModal("a.txt", "/a.txt");
        typeNameAndSubmit(newName);

        expect(await screen.findByText(expected)).toBeInTheDocument();
        expect(moveFile).not.toHaveBeenCalled();
    });

    it("still refuses a name identical to the current one", async () => {
        renderModal("LICENSE", "/LICENSE");
        typeNameAndSubmit("LICENSE");

        expect(await screen.findByText(/same as the current filename/i)).toBeInTheDocument();
        expect(moveFile).not.toHaveBeenCalled();
    });

    it("the mocked API is what the accept cases observe", async () => {
        // Control. Every rejection above asserts `moveFile` was NOT called, which a broken mock or an
        // unrenderable modal satisfies for free. This proves a call is observable in this harness.
        renderModal("a.txt", "/a.txt");
        typeNameAndSubmit("b.txt");
        await waitFor(() => expect(moveFile).toHaveBeenCalled());
    });
});
