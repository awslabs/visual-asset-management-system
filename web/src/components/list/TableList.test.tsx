/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import TableList from "./TableList";

const listDefinition = (over: Record<string, any> = {}) => ({
    columnDefinitions: [
        { id: "databaseId", header: "ID", CellWrapper: ({ children }: any) => <>{children}</> },
    ],
    visibleColumns: ["databaseId"],
    filterColumns: [],
    pluralName: "databases",
    pluralNameTitleCase: "Databases",
    singularNameTitleCase: "Database",
    elementId: "databaseId",
    deleteFunction: jest.fn(),
    ...over,
});

const mount = (definition: any) => {
    const tableList = (allItems: any[]) => (
        <TableList
            allItems={allItems}
            loading={false}
            listDefinition={definition}
            setReload={jest.fn()}
            editEnabled={false}
        />
    );
    // The page mounts the list before its fetch resolves: the cell renderer reads the filter
    // input from the DOM, which exists only after the first commit.
    const utils = render(tableList([]));
    utils.rerender(tableList([{ databaseId: "db-one" }]));
    return utils;
};

/** Selects the single row and opens the delete confirmation. */
const openDelete = () => {
    const table = createWrapper(document.body).findTable()!;
    fireEvent.click(table.findRowSelectionArea(1)!.getElement());
    fireEvent.click(screen.getByRole("button", { name: "Delete Selected" }));
};

describe("TableList delete failure flash", () => {
    it("shows a message the delete function repeats as its detail once", async () => {
        const definition = listDefinition({
            deleteFunction: jest.fn().mockResolvedValue([false, "X", "X"]),
        });
        mount(definition);
        openDelete();
        fireEvent.click(screen.getByRole("button", { name: "Yes" }));

        const flash = await waitFor(() => {
            const item = createWrapper(document.body).findFlashbar()!.findItems()[0];
            expect(item).toBeDefined();
            return item;
        });
        expect(flash.findContent()!.getElement().textContent).toBe("X");
    });

    it("keeps a distinct detail after the message", async () => {
        const definition = listDefinition({
            deleteFunction: jest.fn().mockResolvedValue([false, "Delete failed", "In use"]),
        });
        mount(definition);
        openDelete();
        fireEvent.click(screen.getByRole("button", { name: "Yes" }));

        await waitFor(() =>
            expect(
                createWrapper(document.body)
                    .findFlashbar()!
                    .findItems()[0]
                    .findContent()!
                    .getElement().textContent
            ).toBe("Delete failed. In use")
        );
    });
});

describe("TableList delete dialog", () => {
    it("lists the selected database IDs when the database synonym is renamed", () => {
        mount(listDefinition({ pluralName: "projects", pluralNameTitleCase: "Projects" }));
        openDelete();
        const modal = createWrapper(document.body).findModal()!;
        expect(modal.findContent().getElement().textContent).toContain("db-one");
    });
});

describe("TableList accessible names", () => {
    it("names each row checkbox after the row's first visible column", () => {
        mount(listDefinition());
        expect(screen.getByRole("checkbox", { name: "Select db-one" })).toBeInTheDocument();
        expect(screen.getByRole("checkbox", { name: "Select all" })).toBeInTheDocument();
    });

    it("names an asset row after the asset name rather than its id", () => {
        const definition = listDefinition({
            columnDefinitions: [
                {
                    id: "assetName",
                    header: "Asset",
                    CellWrapper: ({ children }: any) => <>{children}</>,
                },
            ],
            visibleColumns: ["assetName"],
            elementId: "assetId",
            pluralName: "assets",
            pluralNameTitleCase: "Assets",
        });
        const tableList = (allItems: any[]) => (
            <TableList
                allItems={allItems}
                loading={false}
                listDefinition={definition}
                setReload={jest.fn()}
                editEnabled={false}
            />
        );
        const utils = render(tableList([]));
        utils.rerender(tableList([{ assetId: "x1234", assetName: "Turbine blade" }]));
        expect(screen.getByRole("checkbox", { name: "Select Turbine blade" })).toBeInTheDocument();
    });

    it("names the pagination buttons", () => {
        mount(listDefinition());
        expect(screen.getByRole("button", { name: "Next page" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Previous page" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Page 1 of all pages" })).toBeInTheDocument();
    });
});

describe("TableList line wrapping", () => {
    // Cloudscape marks a wrapping body cell with its body-cell-wrap class.
    const wrappedCells = () => document.querySelectorAll("tbody [class*='body-cell-wrap']").length;

    it("keeps cells on one line unless the list definition asks to wrap", () => {
        mount(listDefinition());
        expect(screen.getByText("db-one")).toBeInTheDocument();
        expect(wrappedCells()).toBe(0);
    });

    it("wraps cell text for a list definition with wrapLines", () => {
        mount(listDefinition({ wrapLines: true }));
        expect(screen.getByText("db-one")).toBeInTheDocument();
        expect(wrappedCells()).toBeGreaterThan(0);
    });
});

describe("TableList hidden-by-default columns", () => {
    const twoColumns = (over: Record<string, any> = {}) =>
        listDefinition({
            columnDefinitions: [
                {
                    id: "databaseId",
                    header: "ID",
                    CellWrapper: ({ children }: any) => <>{children}</>,
                },
                {
                    id: "bucketName",
                    header: "Bucket Name",
                    CellWrapper: ({ children }: any) => <>{children}</>,
                },
            ],
            visibleColumns: ["databaseId", "bucketName"],
            ...over,
        });
    const mountRow = (definition: any) => {
        const tableList = (allItems: any[]) => (
            <TableList
                allItems={allItems}
                loading={false}
                listDefinition={definition}
                setReload={jest.fn()}
                editEnabled={false}
            />
        );
        const utils = render(tableList([]));
        utils.rerender(tableList([{ databaseId: "db-one", bucketName: "bucket-one" }]));
        return utils;
    };
    const table = () => createWrapper(document.body).findTable()!;
    const headers = () =>
        table()
            .findColumnHeaders()
            .map((h) => h.getElement().textContent?.trim())
            // The selection checkbox column has no text.
            .filter(Boolean);

    it("shows every column and no preferences control without hiddenColumns", () => {
        mountRow(twoColumns());
        expect(headers()).toEqual(["ID", "Bucket Name"]);
        expect(createWrapper(document.body).findCollectionPreferences()).toBeNull();
    });

    it("starts a hidden column hidden and still finds rows by its value", () => {
        mountRow(twoColumns({ hiddenColumns: ["bucketName"] }));
        expect(headers()).toEqual(["ID"]);
        const filter = createWrapper(document.body).findTextFilter()!;
        filter.findInput().setInputValue("bucket-one");
        expect(screen.getByText("db-one")).toBeInTheDocument();
    });

    it("lets the user show the hidden column again from the preferences", () => {
        mountRow(twoColumns({ hiddenColumns: ["bucketName"] }));
        const prefs = createWrapper(document.body).findCollectionPreferences()!;
        prefs.findTriggerButton().click();
        const modal = createWrapper(document.body).findCollectionPreferences()!.findModal()!;
        const option = modal.findVisibleContentPreference()!.findToggleByIndex(1, 2)!;
        option.findNativeInput().click();
        modal.findConfirmButton()!.click();
        expect(headers()).toEqual(["ID", "Bucket Name"]);
    });
});
