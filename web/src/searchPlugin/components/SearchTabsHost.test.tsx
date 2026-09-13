/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import SearchTabsHost from "./SearchTabsHost";
import type { SearchProviderConfig } from "../core/types";

// The registry uses import.meta.glob, which Jest cannot parse. The instance is built inside the
// hoisted factory and read back through require().
jest.mock("../core/SearchProviderRegistry", () => {
    const instance = {
        getAvailableProviders: jest.fn((): any[] => []),
        loadProvider: jest.fn(async (id: string) => {
            const Body = ({ databaseId, isActive }: any) => (
                <div data-testid={`body-${id}`}>
                    {isActive ? "active" : "inactive"} {databaseId || "all"}
                </div>
            );
            return Body;
        }),
    };
    return { SearchProviderRegistry: { getInstance: () => instance } };
});

const registry = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../core/SearchProviderRegistry").SearchProviderRegistry.getInstance();

const unified: SearchProviderConfig = {
    id: "unified-search",
    name: "Search",
    componentPath: "UnifiedSearchProvider/UnifiedSearchProviderComponent",
    priority: 10,
    availability: { always: true },
};
const assetList: SearchProviderConfig = {
    id: "asset-list",
    name: "Asset List",
    componentPath: "AssetListProvider/AssetListProviderComponent",
    priority: 100,
    availability: { always: true },
};

const LocationProbe = () => <div data-testid="search">{useLocation().search}</div>;

const renderHost = (url: string, databaseId?: string) =>
    render(
        <MemoryRouter initialEntries={[url]}>
            <SearchTabsHost databaseId={databaseId} />
            <LocationProbe />
        </MemoryRouter>
    );

describe("SearchTabsHost", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        registry().getAvailableProviders.mockReturnValue([unified, assetList]);
    });

    it("renders one tab per available provider, lowest priority first and selected", async () => {
        renderHost("/assets");
        const tabs = screen.getAllByRole("tab");
        expect(tabs.map((tab) => tab.textContent)).toEqual(["Search", "Asset List"]);
        expect(tabs[0]).toHaveAttribute("aria-selected", "true");
        await screen.findByTestId("body-unified-search");
        expect(screen.getByTestId("body-unified-search")).toHaveTextContent("active all");
        expect(screen.queryByTestId("body-asset-list")).toBeNull();
    });

    it("selects the tab named by ?tab= and passes the database through", async () => {
        renderHost("/databases/db1/assets?tab=asset-list", "db1");
        expect(screen.getByRole("tab", { name: "Asset List" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        await screen.findByTestId("body-asset-list");
        expect(screen.getByTestId("body-asset-list")).toHaveTextContent("active db1");
    });

    it("falls back to the default tab for an unknown ?tab= value", async () => {
        renderHost("/assets?tab=no-such-provider");
        expect(screen.getByRole("tab", { name: "Search" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
    });

    it("writes the chosen tab into the query string", async () => {
        renderHost("/assets");
        await userEvent.click(screen.getByRole("tab", { name: "Asset List" }));
        await waitFor(() =>
            expect(screen.getByTestId("search")).toHaveTextContent("?tab=asset-list")
        );
        await screen.findByTestId("body-asset-list");
    });

    it("keeps the tab strip with a single provider", () => {
        registry().getAvailableProviders.mockReturnValue([assetList]);
        renderHost("/assets");
        expect(screen.getAllByRole("tab")).toHaveLength(1);
        expect(screen.getByRole("tab", { name: "Asset List" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
    });

    it("explains itself when no provider is available", () => {
        registry().getAvailableProviders.mockReturnValue([]);
        renderHost("/assets");
        expect(screen.queryAllByRole("tab")).toHaveLength(0);
        expect(screen.getByText("Search is not available")).toBeInTheDocument();
    });
});
