/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A source row never offers an asset from a database other than the one selected.
 *
 * The asset picker holds the previous page on screen while the next one loads, so the list does not
 * flash empty on every keystroke. That hold also spans a DATABASE change, where the held page lists
 * another database's assets — and a pick from it records a (database, asset) pair that does not exist.
 * Review shows the pair, Launch is enabled, and the backend's asset lookup 404s the run.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MetadataSourceSelector from "./MetadataSourceSelector";
import type { MetadataSourceAsset } from "../types";

jest.mock("../api/queries", () => ({
    useAssetSearch: jest.fn(),
}));

const queries = () => require("../api/queries");

const DATABASES = [{ databaseId: "db1" }, { databaseId: "db2" }];

/** One page of assets, optionally flagged as the page held from the PREVIOUS query key. */
const assetPage = (names: string[], isPlaceholderData = false) => ({
    data: {
        items: names.map((assetName) => ({
            assetId: assetName.toLowerCase().replace(/\s+/g, "-"),
            assetName,
        })),
        total: names.length,
    },
    isFetching: false,
    isPlaceholderData,
});

const renderFor = (value: MetadataSourceAsset, onChange = jest.fn()) => {
    const view = render(
        <MetadataSourceSelector databaseOptions={DATABASES} value={value} onChange={onChange} />
    );
    const rerenderWith = (next: MetadataSourceAsset) =>
        view.rerender(
            <MetadataSourceSelector databaseOptions={DATABASES} value={next} onChange={onChange} />
        );
    return { onChange, rerenderWith };
};

beforeEach(() => {
    jest.clearAllMocks();
});

describe("MetadataSourceSelector stale database page", () => {
    it("offers no asset while the previous database's page is still being held", async () => {
        queries().useAssetSearch.mockReturnValue(assetPage(["Pump A"]));
        const { rerenderWith } = renderFor({ databaseId: "db1", assetId: "" });
        await userEvent.click(screen.getByLabelText("Metadata source asset"));
        expect(await screen.findByRole("option", { name: /Pump A/ })).toBeInTheDocument();

        // The database changes; the hook is still serving db1's page under the db2 key.
        queries().useAssetSearch.mockReturnValue(assetPage(["Pump A"], true));
        rerenderWith({ databaseId: "db2", assetId: "" });
        await waitFor(() =>
            expect(screen.queryByRole("option", { name: /Pump A/ })).not.toBeInTheDocument()
        );
    });

    it("offers the new database's assets once its own page arrives", async () => {
        queries().useAssetSearch.mockReturnValue(assetPage(["Pump A"]));
        const { rerenderWith } = renderFor({ databaseId: "db1", assetId: "" });
        await userEvent.click(screen.getByLabelText("Metadata source asset"));
        await screen.findByRole("option", { name: /Pump A/ });

        queries().useAssetSearch.mockReturnValue(assetPage(["Valve C"]));
        rerenderWith({ databaseId: "db2", assetId: "" });
        expect(await screen.findByRole("option", { name: /Valve C/ })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: /Pump A/ })).not.toBeInTheDocument();
    });

    it("reads as loading while the held page is suppressed, not as an empty database", async () => {
        // The distinction the user acts on: "still resolving" invites a wait, "no assets" invites
        // giving up on a database that does have assets.
        queries().useAssetSearch.mockReturnValue(assetPage(["Pump A"]));
        const { rerenderWith } = renderFor({ databaseId: "db1", assetId: "" });
        await userEvent.click(screen.getByLabelText("Metadata source asset"));
        await screen.findByRole("option", { name: /Pump A/ });

        queries().useAssetSearch.mockReturnValue(assetPage(["Pump A"], true));
        rerenderWith({ databaseId: "db2", assetId: "" });
        expect(await screen.findByText("Searching…")).toBeInTheDocument();
        expect(screen.queryByText("No matches")).not.toBeInTheDocument();
    });

    it("keeps emitting the row's own database with the chosen asset", async () => {
        queries().useAssetSearch.mockReturnValue(assetPage(["Valve C"]));
        const { onChange } = renderFor({ databaseId: "db2", assetId: "" });
        await userEvent.click(screen.getByLabelText("Metadata source asset"));
        await userEvent.click(await screen.findByRole("option", { name: /Valve C/ }));
        expect(onChange).toHaveBeenCalledWith({ databaseId: "db2", assetId: "valve-c" });
    });
});
