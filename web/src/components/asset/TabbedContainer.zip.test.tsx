/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TabbedContainer from "./TabbedContainer";
import { appCache } from "../../services/appCache";

jest.mock("./tabs/FileManagerTab", () => ({ __esModule: true, default: () => <div /> }));
jest.mock("./tabs/AssetLinksTab", () => ({ __esModule: true, default: () => <div /> }));
jest.mock("./tabs/AssetExecutionsTab", () => ({ __esModule: true, default: () => <div /> }));
jest.mock("./tabs/CommentsTab", () => ({ __esModule: true, default: () => <div /> }));
jest.mock("./tabs/VersionsTab", () => ({ __esModule: true, default: () => <div /> }));
// The tab's body records whether it is the active one (it loads and polls only then).
jest.mock("./tabs/ZipDownloadsTab", () => ({
    __esModule: true,
    default: ({ databaseId, assetId, isActive }: any) => (
        <div data-testid="zip-tab">{`${databaseId}/${assetId}/${isActive}`}</div>
    ),
}));

const renderContainer = () =>
    render(<TabbedContainer assetName="A" assetId="a1" databaseId="db1" />);

describe("TabbedContainer — My zip downloads tab", () => {
    afterEach(() => localStorage.clear());

    it("is listed when the deployment has the feature, and active only once chosen", async () => {
        appCache.setItem("config", { featuresEnabled: ["ZIPDOWNLOADS"] });
        renderContainer();
        const tab = screen.getByRole("tab", { name: "My zip downloads" });
        expect(screen.queryByTestId("zip-tab")).toBeNull();
        await userEvent.click(tab);
        expect(await screen.findByTestId("zip-tab")).toHaveTextContent("db1/a1/true");
    });

    it("is absent when the deployment has the feature off", () => {
        appCache.setItem("config", { featuresEnabled: ["LOCATIONSERVICES"] });
        renderContainer();
        expect(screen.getByRole("tab", { name: "Versions" })).toBeInTheDocument();
        expect(screen.queryByRole("tab", { name: "My zip downloads" })).toBeNull();
    });
});
