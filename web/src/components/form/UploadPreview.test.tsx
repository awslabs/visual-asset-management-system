/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The upload preview table (#1462): its pager is labelled for assistive technology.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
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
    it("D1: the pager's arrows and pages have accessible names", () => {
        renderPreview(looseFiles(120));
        expect(screen.getByRole("button", { name: "Next page" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Previous page" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Go to page 2" })).toBeTruthy();
    });
});
