/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The viewer used to seed its <img src> with "placeholder.jpg" before the presigned URL resolved.
 * No such file ships in the web bundle, so every image opened produced a real request for
 * /placeholder.jpg that failed, and that failure also tripped the component's error state. The image
 * element must not be rendered until there is a URL to give it.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import ImageViewerComponent from "./ImageViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

const props = {
    assetId: "a1",
    databaseId: "d1",
    assetKey: "/images/pic.png",
} as any;

describe("ImageViewerComponent", () => {
    beforeEach(() => jest.clearAllMocks());

    it("never requests a placeholder image while the URL is resolving", async () => {
        // A never-settling download keeps the component in its pre-URL state for the assertion.
        mockDownloadAsset.mockReturnValue(new Promise(() => {}));

        render(<ImageViewerComponent {...props} />);

        expect(document.querySelector("img")).toBeNull();
        const srcs = Array.from(document.querySelectorAll("[src]")).map((n) =>
            n.getAttribute("src")
        );
        expect(srcs.some((s) => (s || "").includes("placeholder"))).toBe(false);
        expect(screen.getByText(/loading image/i)).toBeInTheDocument();
    });

    it("renders the image once the presigned URL resolves", async () => {
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/pic.png?sig=1"]);

        render(<ImageViewerComponent {...props} />);

        await waitFor(() => expect(document.querySelector("img")).not.toBeNull());
        expect(document.querySelector("img")?.getAttribute("src")).toBe(
            "https://example.test/pic.png?sig=1"
        );
    });

    it("shows an error message instead of a broken image when the download fails", async () => {
        mockDownloadAsset.mockResolvedValue([false, "denied"]);

        render(<ImageViewerComponent {...props} />);

        await waitFor(() =>
            expect(screen.getByText(/unable to load this image/i)).toBeInTheDocument()
        );
        expect(document.querySelector("img")).toBeNull();
    });

    it("does not hang on the loading state when there is no file key", async () => {
        render(<ImageViewerComponent {...({ ...props, assetKey: "" } as any)} />);

        await waitFor(() =>
            expect(screen.getByText(/unable to load this image/i)).toBeInTheDocument()
        );
        expect(mockDownloadAsset).not.toHaveBeenCalled();
    });
});
