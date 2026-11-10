/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigationType } from "react-router-dom";
import SearchTabsHost, { SEARCH_VIEW_SWITCH_LABEL } from "./SearchTabsHost";
import type { SearchProviderConfig } from "../core/types";

// The registry uses import.meta.glob, which Jest cannot parse. The instance is built inside the
// hoisted factory and read back through require(). Each body reports its props and adds a count and
// an action to the page's title row through the slots, as the Search view does.
jest.mock("../core/SearchProviderRegistry", () => {
    const instance = {
        getAvailableProviders: jest.fn((): any[] => []),
        loadProvider: jest.fn(async (id: string) => {
            const Body = ({
                databaseId,
                isActive,
                assetActionDisabledReasons,
                titleSlots,
            }: any) => {
                // eslint-disable-next-line @typescript-eslint/no-var-requires
                const { createPortal } = require("react-dom");
                return (
                    <div
                        data-testid={`body-${id}`}
                        data-reasons={JSON.stringify(assetActionDisabledReasons ?? null)}
                    >
                        {isActive ? "active" : "inactive"} {databaseId || "all"}
                        {titleSlots?.info &&
                            createPortal(
                                <span data-testid={`count-${id}`}>4 results</span>,
                                titleSlots.info
                            )}
                        {titleSlots?.actions &&
                            createPortal(
                                <button type="button">Action of {id}</button>,
                                titleSlots.actions
                            )}
                    </div>
                );
            };
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
    name: "Asset list",
    componentPath: "AssetListProvider/AssetListProviderComponent",
    priority: 100,
    availability: { always: true },
};

const LocationProbe = () => (
    <div data-testid="search" data-navigation={useNavigationType()}>
        {useLocation().search}
    </div>
);

const renderHost = (url: string, databaseId?: string) =>
    render(
        <MemoryRouter initialEntries={[url]}>
            <SearchTabsHost databaseId={databaseId} />
            <LocationProbe />
        </MemoryRouter>
    );

const viewSwitch = () => screen.getByRole("toolbar", { name: SEARCH_VIEW_SWITCH_LABEL });
const segment = (name: string) => within(viewSwitch()).getByRole("button", { name });
const pageHeadings = () => screen.getAllByRole("heading", { level: 1 });
/** True when `later` comes after `earlier` in document order. */
const follows = (earlier: Element, later: Element) =>
    !!(earlier.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING);

describe("SearchTabsHost", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        registry().getAvailableProviders.mockReturnValue([unified, assetList]);
    });

    it("renders no tab strip: a view switch in the title row, lowest priority first and selected", async () => {
        renderHost("/assets");
        expect(screen.queryByRole("tablist")).toBeNull();
        expect(screen.queryAllByRole("tab")).toHaveLength(0);
        const segments = within(viewSwitch()).getAllByRole("button");
        expect(segments.map((button) => button.textContent)).toEqual(["Search", "Asset list"]);
        expect(segments[0]).toHaveAttribute("aria-pressed", "true");
        expect(segments[1]).toHaveAttribute("aria-pressed", "false");
        await screen.findByTestId("body-unified-search");
        expect(screen.getByTestId("body-unified-search")).toHaveTextContent("active all");
        expect(screen.queryByTestId("body-asset-list")).toBeNull();
    });

    it("renders one h1 per view, the page title, with the switch in its row", async () => {
        renderHost("/assets");
        await screen.findByTestId("body-unified-search");
        expect(pageHeadings()).toHaveLength(1);
        expect(pageHeadings()[0]).toHaveTextContent(/^Assets and Files$/);
        // The switch follows the h1 in its row and comes before the body, outside it.
        const body = screen.getByTestId("body-unified-search");
        expect(follows(pageHeadings()[0], viewSwitch())).toBe(true);
        expect(follows(viewSwitch(), body)).toBe(true);
        expect(body).not.toContainElement(viewSwitch());

        await userEvent.click(segment("Asset list"));
        await screen.findByTestId("body-asset-list");
        expect(pageHeadings()).toHaveLength(1);
        expect(pageHeadings()[0]).toHaveTextContent(/^Assets and Files$/);
    });

    it("titles a database-scoped page by its database", async () => {
        renderHost("/databases/db1/assets?tab=asset-list", "db1");
        await screen.findByTestId("body-asset-list");
        expect(pageHeadings()).toHaveLength(1);
        expect(pageHeadings()[0]).toHaveTextContent(/^Assets for db1$/);
    });

    it("selects the view named by ?tab= and passes the database through", async () => {
        renderHost("/databases/db1/assets?tab=asset-list", "db1");
        expect(segment("Asset list")).toHaveAttribute("aria-pressed", "true");
        expect(segment("Search")).toHaveAttribute("aria-pressed", "false");
        await screen.findByTestId("body-asset-list");
        expect(screen.getByTestId("body-asset-list")).toHaveTextContent("active db1");
        expect(screen.queryByTestId("body-unified-search")).toBeNull();
    });

    it("passes the page's asset action reasons to the provider body", async () => {
        const reasons = { create: "No create.", delete: "No delete." };
        render(
            <MemoryRouter initialEntries={["/assets?tab=asset-list"]}>
                <SearchTabsHost assetActionDisabledReasons={reasons} />
            </MemoryRouter>
        );
        const body = await screen.findByTestId("body-asset-list");
        expect(JSON.parse(body.getAttribute("data-reasons") as string)).toEqual(reasons);
    });

    it("places a body's count beside the h1 and its action before the switch", async () => {
        renderHost("/assets");
        const count = await screen.findByTestId("count-unified-search");
        const action = await screen.findByRole("button", { name: "Action of unified-search" });
        const heading = pageHeadings()[0];
        // The count sits after the heading text, outside the h1, so the h1's name stays the title.
        expect(heading).not.toContainElement(count);
        expect(follows(heading, count)).toBe(true);
        expect(follows(count, action)).toBe(true);
        expect(follows(action, viewSwitch())).toBe(true);
        expect(screen.getByTestId("body-unified-search")).not.toContainElement(action);

        // The next view brings its own title-row content; the previous view's leaves with it.
        await userEvent.click(segment("Asset list"));
        await screen.findByTestId("count-asset-list");
        expect(screen.queryByTestId("count-unified-search")).toBeNull();
        expect(screen.queryByRole("button", { name: "Action of unified-search" })).toBeNull();
    });

    it("falls back to the default view for an unknown ?tab= value", async () => {
        renderHost("/assets?tab=no-such-provider");
        expect(segment("Search")).toHaveAttribute("aria-pressed", "true");
        await screen.findByTestId("body-unified-search");
    });

    it("writes the chosen view into the query string, replacing the history entry", async () => {
        renderHost("/assets");
        await userEvent.click(segment("Asset list"));
        await waitFor(() =>
            expect(screen.getByTestId("search")).toHaveTextContent("?tab=asset-list")
        );
        expect(screen.getByTestId("search")).toHaveAttribute("data-navigation", "REPLACE");
        await screen.findByTestId("body-asset-list");
        expect(screen.queryByTestId("body-unified-search")).toBeNull();
    });

    it("keeps keyboard focus on the switch after a switch, so the user can switch back", async () => {
        renderHost("/assets");
        await screen.findByTestId("body-unified-search");
        const toList = segment("Asset list");
        await userEvent.click(toList);
        await screen.findByTestId("body-asset-list");
        // The same control is still mounted and still holds focus; the body under it was replaced.
        expect(toList.isConnected).toBe(true);
        expect(toList).toHaveFocus();
        expect(toList).toHaveAttribute("aria-pressed", "true");

        // Back by keyboard: the arrow moves along the switch and Enter selects. Cloudscape reads the
        // arrow's keyCode, which user-event's keyboard() does not set.
        fireEvent.keyDown(toList, { key: "ArrowLeft", keyCode: 37 });
        const toSearch = segment("Search");
        expect(toSearch).toHaveFocus();
        await userEvent.keyboard("{Enter}");
        await screen.findByTestId("body-unified-search");
        expect(screen.getByTestId("search")).toHaveTextContent("?tab=unified-search");
        expect(toSearch.isConnected).toBe(true);
        expect(toSearch).toHaveFocus();
        expect(toSearch).toHaveAttribute("aria-pressed", "true");
    });

    it("shows no switch with a single provider, and still titles the page", async () => {
        registry().getAvailableProviders.mockReturnValue([assetList]);
        renderHost("/assets");
        expect(screen.queryByRole("toolbar", { name: SEARCH_VIEW_SWITCH_LABEL })).toBeNull();
        expect(screen.queryByRole("tablist")).toBeNull();
        expect(pageHeadings()).toHaveLength(1);
        expect(pageHeadings()[0]).toHaveTextContent(/^Assets and Files$/);
        await screen.findByTestId("body-asset-list");
    });

    it("explains itself when no provider is available", () => {
        registry().getAvailableProviders.mockReturnValue([]);
        renderHost("/assets");
        expect(screen.queryByRole("toolbar", { name: SEARCH_VIEW_SWITCH_LABEL })).toBeNull();
        expect(screen.getByText("Search is not available")).toBeInTheDocument();
    });

    // A provider body is the search page's content, so its chunk recovers like a route page's
    // (#1035): a chunk that fails to download is fetched again before anything is reported.
    it("fetches a provider body again when its chunk fails to download", async () => {
        // An id of its own: the host keeps one lazy component per provider id for the module's life.
        const flaky: SearchProviderConfig = { ...unified, id: "flaky-provider", name: "Flaky" };
        registry().getAvailableProviders.mockReturnValue([flaky]);
        const loadBody = registry().loadProvider.getMockImplementation();
        registry().loadProvider.mockImplementationOnce(() =>
            Promise.reject(
                new TypeError(
                    "Failed to fetch dynamically imported module: https://vams.example/assets/UnifiedSearch-0ld.js"
                )
            )
        );
        const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        try {
            renderHost("/assets");
            expect(
                await screen.findByTestId("body-flaky-provider", {}, { timeout: 5_000 })
            ).toHaveTextContent("active all");
            expect(screen.queryByText(/could not be downloaded/)).toBeNull();
            expect(registry().loadProvider).toHaveBeenCalledTimes(2);
        } finally {
            warn.mockRestore();
            registry().loadProvider.mockImplementation(loadBody);
        }
    }, 10_000);
});
