/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The upload preview table (#1462): its pager is labelled for assistive technology.
 */
import React from "react";
import { render, screen, fireEvent, within } from "@testing-library/react";
import UploadPreview from "./UploadPreview";
import { UploadRoot, planUploadPaths } from "./uploadPaths";

const looseFiles = (count: number): UploadRoot[] =>
    Array.from({ length: count }, (_, i) => {
        const name = `file-${String(i).padStart(3, "0")}.txt`;
        return { kind: "file", name, handle: new File(["x"], name) } as UploadRoot;
    });

const renderPreview = (roots: UploadRoot[]) => {
    const planned = planUploadPaths(roots, {
        flow: "createAsset",
        destination: "/",
        folderNameMode: "auto",
    });
    render(
        <UploadPreview
            flow="createAsset"
            destination="/"
            roots={roots}
            planned={planned}
            folderNameMode="auto"
            onFolderNameModeChange={jest.fn()}
            onRemove={jest.fn()}
            onRemoveAll={jest.fn()}
        />
    );
};

describe("UploadPreview", () => {
    it("offers the Existing view on Add Files only once the existing-path check has finished", () => {
        const roots = looseFiles(2);
        const planned = planUploadPaths(roots, {
            flow: "addFiles",
            destination: "/s/",
            folderNameMode: "auto",
        });
        const props = {
            flow: "addFiles" as const,
            destination: "/s/",
            roots,
            planned,
            folderNameMode: "auto" as const,
            onFolderNameModeChange: jest.fn(),
            onRemove: jest.fn(),
            onRemoveAll: jest.fn(),
        };
        const { rerender } = render(<UploadPreview {...props} existingCheck="checking" />);
        expect(screen.queryByText(/^Existing \(/)).toBeNull();
        expect(screen.getByText(/^Errors \(0\)/)).toBeTruthy();
        rerender(<UploadPreview {...props} existingCheck="done" />);
        expect(screen.getByText("Existing (0)")).toBeTruthy();
    });

    it("D1: the pager's arrows and pages have accessible names", () => {
        renderPreview(looseFiles(120));
        expect(screen.getByRole("button", { name: "Next page" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Previous page" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Go to page 2" })).toBeTruthy();
    });

    it("D2: states path errors above the table and filters to them in one click", () => {
        const dup: UploadRoot[] = [
            ...looseFiles(60),
            { kind: "file", name: "file-000.txt", handle: new File(["x"], "file-000.txt") },
        ];
        renderPreview(dup);
        const alert = screen.getByText(/2 files have path errors/);
        expect(alert).toBeTruthy();
        // The duplicate rows sort to the top, but the count is stated outside the table either way.
        fireEvent.click(screen.getByRole("button", { name: "Show errors only" }));
        expect(screen.getAllByText("Duplicate path")).toHaveLength(2);
        expect(screen.queryByText("/file-059.txt")).toBeNull();
        expect(screen.queryByRole("button", { name: "Show errors only" })).toBeNull();
    });

    it("D2: shows each row's status and size inside its path cell, with an icon-only remove", () => {
        const name = `${"very-long-segment-".repeat(6)}model.gltf`;
        const dupName = "a.txt";
        renderPreview([
            { kind: "file", name, handle: new File(["x"], name) },
            { kind: "file", name: dupName, handle: new File(["x"], dupName) },
            { kind: "file", name: dupName, handle: new File(["x"], dupName) },
        ]);
        const cells = screen.getAllByTestId("upload-preview-file-cell");
        expect(cells).toHaveLength(3);
        const longCell = cells.find((c) => c.textContent?.includes(`/${name}`)) as HTMLElement;
        expect(within(longCell).getByText(`/${name}`).style.overflowWrap).toBe("anywhere");
        expect(within(longCell).getByText("Ready")).toBeTruthy();
        expect(within(longCell).getByText("1 B")).toBeTruthy();
        const dupCells = cells.filter((c) => c.textContent?.includes("/a.txt"));
        dupCells.forEach((c) => expect(within(c).getByText("Duplicate path")).toBeTruthy());

        // No separate Size or Status columns, and the remove column has no text header.
        const headers = screen.getAllByRole("columnheader").map((h) => h.textContent?.trim());
        expect(headers).not.toContain("Size");
        expect(headers).not.toContain("Status");
        expect(headers).not.toContain("Remove");
        expect(screen.getByRole("img", { name: "Remove" })).toBeTruthy();
        expect(screen.getByRole("button", { name: `Remove /${name}` })).toBeTruthy();
        expect(screen.getAllByRole("button", { name: "Remove /a.txt" })).toHaveLength(2);
    });
});
