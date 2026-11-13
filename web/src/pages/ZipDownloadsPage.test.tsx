/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { appCache } from "../services/appCache";
import ZipDownloadsPage from "./ZipDownloadsPage";

// The table is covered by its own tests; the page's layout is the subject here.
jest.mock("../components/zipDownloads/ZipDownloadsTable", () => ({
    __esModule: true,
    default: () => <div data-testid="download-queue-table" />,
}));

describe("ZipDownloadsPage (My Download Queue)", () => {
    afterEach(() => localStorage.clear());

    it("opens with the page shell, an h1 and the document title, then the table", () => {
        appCache.setItem("config", { featuresEnabled: ["ZIPDOWNLOADS"] });
        const { container } = render(<ZipDownloadsPage />);

        const heading = screen.getByRole("heading", { level: 1, name: "My Download Queue" });
        const table = screen.getByTestId("download-queue-table");
        // The page's outermost element is the padded shell, and it holds both the h1 and the
        // table, so the table never sits flush against the side navigation.
        const shell = container.firstElementChild as HTMLElement;
        expect(shell).toContainElement(heading);
        expect(shell).toContainElement(table);
        expect(shell.className).toMatch(/\bawsui_p-top-m_/);
        expect(shell.className).toMatch(/\bawsui_p-horizontal-l_/);
        expect(document.title).toBe("VAMS - My Download Queue");
        expect(screen.queryByText(/zip downloads/i)).toBeNull();
    });

    it("keeps the shell and heading when the deployment has the feature off", () => {
        appCache.setItem("config", { featuresEnabled: [] });
        const { container } = render(<ZipDownloadsPage />);
        expect(
            screen.getByRole("heading", { level: 1, name: "My Download Queue" })
        ).toBeInTheDocument();
        expect(screen.getByText("The Download Queue is not available")).toBeInTheDocument();
        expect(screen.queryByTestId("download-queue-table")).toBeNull();
        expect((container.firstElementChild as HTMLElement).className).toMatch(
            /\bawsui_p-horizontal-l_/
        );
    });
});
