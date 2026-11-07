/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * ListPage renders its own breadcrumbs (on a database route) and h1 for the pages that use it
 * directly, such as Databases. A host page that renders those itself, the search page's asset list
 * view, opts out with hidePageHeading; the table is unaffected.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ListPage from "./ListPage";

jest.mock("../components/list/TableList", () => ({
    __esModule: true,
    default: () => <div data-testid="table-list" />,
}));

const renderListPage = (url: string, extra: Record<string, unknown> = {}) =>
    render(
        <MemoryRouter initialEntries={[url]}>
            <Routes>
                {["/databases", "/databases/:databaseId/assets"].map((path) => (
                    <Route
                        key={path}
                        path={path}
                        element={
                            <ListPage
                                singularName="database"
                                singularNameTitleCase="Database"
                                pluralName="databases"
                                pluralNameTitleCase="Databases"
                                listDefinition={
                                    { elementId: "databaseId", columnDefinitions: [] } as any
                                }
                                fetchElements={jest.fn().mockResolvedValue([])}
                                fetchAllElements={jest.fn().mockResolvedValue([])}
                                {...extra}
                            />
                        }
                    />
                ))}
            </Routes>
        </MemoryRouter>
    );

describe("ListPage page heading", () => {
    it("keeps its h1 for the pages that use it directly", async () => {
        renderListPage("/databases");
        await waitFor(() => expect(screen.getByTestId("table-list")).toBeInTheDocument());
        expect(screen.getByRole("heading", { level: 1, name: "Databases" })).toBeInTheDocument();
    });

    it("keeps its breadcrumbs and database h1 on a database route", async () => {
        renderListPage("/databases/db1/assets");
        await waitFor(() => expect(screen.getByTestId("table-list")).toBeInTheDocument());
        expect(screen.getByRole("navigation", { name: "Breadcrumbs" })).toBeInTheDocument();
        expect(
            screen.getByRole("heading", { level: 1, name: "Databases for db1" })
        ).toBeInTheDocument();
    });

    it("hides the breadcrumbs and the h1, not the table, with hidePageHeading", async () => {
        renderListPage("/databases/db1/assets", { hidePageHeading: true });
        await waitFor(() => expect(screen.getByTestId("table-list")).toBeInTheDocument());
        expect(screen.queryAllByRole("heading", { level: 1 })).toHaveLength(0);
        expect(screen.queryByRole("navigation", { name: "Breadcrumbs" })).toBeNull();
    });
});
