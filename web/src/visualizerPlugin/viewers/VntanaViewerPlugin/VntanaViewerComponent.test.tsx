/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The VNTANA viewer's download-failure message (#1211) is decided only by the status the API
 * returned in the `downloadAsset` tuple: a 403 names the missing permission, a 401 asks the user to
 * sign in again, and anything else keeps the generic wording.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import {
    DOWNLOAD_FORBIDDEN_MESSAGE,
    DOWNLOAD_UNAUTHENTICATED_MESSAGE,
} from "../../core/streamFailure";
import VntanaViewerComponent from "./VntanaViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));
jest.mock("./dependencies", () => ({
    VntanaDependencyManager: { loadVntana: jest.fn().mockResolvedValue({}) },
}));

const props = { assetId: "a1", databaseId: "d1", assetKey: "/model.glb" } as any;

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
    jest.restoreAllMocks();
    mockDownloadAsset.mockReset();
});

describe("VntanaViewerComponent download failures (#1211)", () => {
    it("names the missing permission when the API refuses the download (403)", async () => {
        mockDownloadAsset.mockResolvedValue([false, "Not Authorized", 403]);
        render(<VntanaViewerComponent {...props} />);
        expect(await screen.findByText(DOWNLOAD_FORBIDDEN_MESSAGE)).toBeInTheDocument();
        expect(screen.getByText("Error Loading Model")).toBeInTheDocument();
        expect(screen.queryByText("Not Authorized")).not.toBeInTheDocument();
    });

    it("asks a signed-out user to sign in again (401), not that permission is missing", async () => {
        mockDownloadAsset.mockResolvedValue([false, "Unauthorized", 401]);
        render(<VntanaViewerComponent {...props} />);
        expect(await screen.findByText(DOWNLOAD_UNAUTHENTICATED_MESSAGE)).toBeInTheDocument();
        expect(screen.queryByText(DOWNLOAD_FORBIDDEN_MESSAGE)).not.toBeInTheDocument();
    });

    it("[control] keeps the generic wording when the response carries no status", async () => {
        mockDownloadAsset.mockResolvedValue([false, "Request blocked"]);
        render(<VntanaViewerComponent {...props} />);
        expect(await screen.findByText("Failed to download asset")).toBeInTheDocument();
    });
});
