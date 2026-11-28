/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The loading and error messages sit over the striped viewer background, so each is drawn on its
 * own theme-token panel.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import AudioViewerComponent from "./AudioViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

const props = { assetId: "a1", databaseId: "d1", assetKey: "media/clip.mp3" } as any;

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    jest.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("AudioViewerComponent messages", () => {
    it("draws the loading message on a theme panel", () => {
        mockDownloadAsset.mockReturnValue(new Promise(() => undefined));
        render(<AudioViewerComponent {...props} />);
        const message = screen.getByText("Loading audio...");
        expect(message.style.background).toBe("var(--vams-bg-primary)");
        expect(message.style.color).toBe("var(--vams-text-secondary)");
    });

    it("draws the error message on a theme panel", async () => {
        mockDownloadAsset.mockResolvedValue([false, "Forbidden"]);
        render(<AudioViewerComponent {...props} />);
        const message = await screen.findByText(/Error: Failed to download audio file/);
        expect(message.style.background).toBe("var(--vams-bg-primary)");
        expect(message.style.color).toBe("var(--vams-color-error)");
    });
});
