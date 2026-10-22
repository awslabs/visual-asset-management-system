/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The database breadcrumb on a database's asset list links to the asset route, which is always
 * /assets/ whatever display term the deployment uses for assets.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ListPage from "./ListPage";

jest.mock("../components/list/TableList", () => ({
    __esModule: true,
    default: () => <div data-testid="table-list" />,
}));

describe("ListPage breadcrumb", () => {
    it("links the database crumb to the assets route when the asset term is renamed", async () => {
        render(
            <MemoryRouter initialEntries={["/databases/db1/assets"]}>
                <Routes>
                    <Route
                        path="/databases/:databaseId/assets"
                        element={
                            <ListPage
                                singularName="model"
                                singularNameTitleCase="Model"
                                pluralName="models"
                                pluralNameTitleCase="Models"
                                listDefinition={
                                    { elementId: "assetId", columnDefinitions: [] } as any
                                }
                                fetchElements={jest.fn().mockResolvedValue([])}
                                fetchAllElements={jest.fn().mockResolvedValue([])}
                            />
                        }
                    />
                </Routes>
            </MemoryRouter>
        );

        await waitFor(() => expect(screen.getByTestId("table-list")).toBeInTheDocument());
        const links = screen
            .getAllByText("db1")
            .map((element) => element.closest("a"))
            .filter((link): link is HTMLAnchorElement => link !== null);
        expect(links.length).toBeGreaterThan(0);
        links.forEach((link) => expect(link).toHaveAttribute("href", "#/databases/db1/assets/"));
    });
});
