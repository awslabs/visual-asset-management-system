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
jest.mock("./tabs/ComplianceTab", () => ({
    __esModule: true,
    default: ({ isActive }: any) => <div data-testid="compliance-tab">{`${isActive}`}</div>,
}));
// The tab's body records whether it is the active one (it loads and polls only then).
jest.mock("./tabs/ZipDownloadsTab", () => ({
    __esModule: true,
    default: ({ databaseId, assetId, isActive }: any) => (
        <div data-testid="zip-tab">{`${databaseId}/${assetId}/${isActive}`}</div>
    ),
}));

const renderContainer = () =>
    render(<TabbedContainer assetName="A" assetId="a1" databaseId="db1" />);

describe("TabbedContainer — Download Queue tab", () => {
    afterEach(() => localStorage.clear());

    it("is listed when the deployment has the feature, and active only once chosen", async () => {
        appCache.setItem("config", { featuresEnabled: ["ZIPDOWNLOADS"] });
        renderContainer();
        const tab = screen.getByRole("tab", { name: "Download Queue" });
        expect(screen.queryByTestId("zip-tab")).toBeNull();
        await userEvent.click(tab);
        expect(await screen.findByTestId("zip-tab")).toHaveTextContent("db1/a1/true");
    });

    it("is absent when the deployment has the feature off", () => {
        appCache.setItem("config", { featuresEnabled: ["LOCATIONSERVICES"] });
        renderContainer();
        expect(screen.getByRole("tab", { name: "Versions" })).toBeInTheDocument();
        expect(screen.queryByRole("tab", { name: "Download Queue" })).toBeNull();
    });
});

describe("TabbedContainer — tab selected by the page's ?tab= query", () => {
    afterEach(() => localStorage.clear());

    const props = { assetName: "A", assetId: "a1", databaseId: "db1" };

    it("opens on the Download Queue tab when the page is opened with tab=zipDownloads", async () => {
        appCache.setItem("config", { featuresEnabled: ["ZIPDOWNLOADS"] });
        render(<TabbedContainer {...props} requestedTabId="zipDownloads" />);
        expect(screen.getByRole("tab", { name: "Download Queue" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(await screen.findByTestId("zip-tab")).toHaveTextContent("db1/a1/true");
    });

    it("switches to the tab when the query changes, without remounting the container", async () => {
        appCache.setItem("config", { featuresEnabled: ["ZIPDOWNLOADS"] });
        const { rerender } = render(<TabbedContainer {...props} />);
        const fileManagerTab = screen.getByRole("tab", { name: "File Manager" });
        expect(fileManagerTab).toHaveAttribute("aria-selected", "true");
        rerender(<TabbedContainer {...props} requestedTabId="zipDownloads" />);
        // Same tab element: the container was re-rendered in place, not mounted again.
        expect(screen.getByRole("tab", { name: "File Manager" })).toBe(fileManagerTab);
        expect(screen.getByRole("tab", { name: "Download Queue" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(await screen.findByTestId("zip-tab")).toHaveTextContent("db1/a1/true");
        // The query going away (Back) returns to the File Manager.
        rerender(<TabbedContainer {...props} requestedTabId={null} />);
        expect(screen.getByRole("tab", { name: "File Manager" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
    });

    it("ignores an unknown tab, and the Download Queue tab when the feature is off", () => {
        appCache.setItem("config", { featuresEnabled: [] });
        render(<TabbedContainer {...props} requestedTabId="zipDownloads" />);
        expect(screen.getByRole("tab", { name: "File Manager" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
    });

    // Development's Compliance tab (#749) is a known tab like the others, so tab=compliance opens it.
    it("opens on the Compliance tab when the page is opened with tab=compliance", async () => {
        appCache.setItem("config", { featuresEnabled: [] });
        render(<TabbedContainer {...props} requestedTabId="compliance" />);
        expect(screen.getByRole("tab", { name: "Compliance" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(await screen.findByTestId("compliance-tab")).toHaveTextContent("true");
    });

    it("reports a tab the user picks, so the page can keep ?tab= in step", async () => {
        appCache.setItem("config", { featuresEnabled: ["ZIPDOWNLOADS"] });
        const onTabChange = jest.fn();
        render(<TabbedContainer {...props} onTabChange={onTabChange} />);
        await userEvent.click(screen.getByRole("tab", { name: "Versions" }));
        expect(onTabChange).toHaveBeenCalledWith("versions");
    });
});
