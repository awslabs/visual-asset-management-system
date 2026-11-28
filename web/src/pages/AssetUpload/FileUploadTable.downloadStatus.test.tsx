/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The folder download's table is wider than the page, so a failed file's reason must not sit in its last column
 * (#2074, review L2: off-screen at 1280 px). In Download mode the Status column comes first and its text wraps
 * in the cell; the upload table keeps its columns as they were.
 */

import React from "react";
import { render } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { FileUploadTable, FileUploadTableItem } from "./FileUploadTable";

const REASON =
    "the target folder already has a folder where this file goes, or a file where its folder goes";

const items: FileUploadTableItem[] = [
    {
        name: "a.txt",
        index: 0,
        size: 4,
        relativePath: "/a.txt",
        status: "Failed",
        progress: 0,
        loaded: 0,
        total: 4,
        error: REASON,
        finalDownloadPath: "picked/a.txt",
    },
    {
        name: "b.txt",
        index: 1,
        size: 4,
        relativePath: "/a.txt/b.txt",
        status: "Completed",
        progress: 100,
        loaded: 4,
        total: 4,
        finalDownloadPath: "picked/a.txt/b.txt",
    },
];

const headers = (container: HTMLElement) => {
    const table = createWrapper(container).findTable();
    if (!table) throw new Error("no table");
    return table.findColumnHeaders().map((header) => header.getElement().textContent?.trim() || "");
};

describe("FileUploadTable in Download mode", () => {
    it("shows the Status column, with a failed file's reason, first, and wraps it", () => {
        const { container } = render(
            <FileUploadTable allItems={items} resume={false} mode="Download" />
        );

        expect(headers(container)[0]).toMatch(/^Status/);
        // The first cell of the failed file's row holds its status and reason
        const table = createWrapper(container).findTable();
        const rows = table?.findRows() ?? [];
        const failedRow = rows.findIndex((row) => row.getElement().textContent?.includes("Failed"));
        expect(failedRow).toBeGreaterThanOrEqual(0);
        expect(table?.findBodyCell(failedRow + 1, 1)?.getElement().textContent).toContain(
            `Failed: ${REASON}`
        );
        expect(container.querySelector("[class*='body-cell-wrap']")).not.toBeNull();
    });

    it("leaves the upload table's columns and lines as they were", () => {
        const { container } = render(
            <FileUploadTable allItems={items} resume={false} mode="Upload" />
        );

        const names = headers(container);
        expect(names[0]).toMatch(/^Progress/);
        expect(names[names.length - 1]).toMatch(/^Status/);
        expect(container.querySelector("[class*='body-cell-wrap']")).toBeNull();
    });
});
