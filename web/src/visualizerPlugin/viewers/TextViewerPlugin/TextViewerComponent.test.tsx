/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Text Viewer is offered by file NAME, including files with no extension (#1198), so it can be
 * handed binary bytes. It reads the first 8 KiB, shows a notice instead of the highlighter when they
 * are not text, and decodes UTF-16 files that carry a byte-order mark.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import TextViewerComponent, { TEXT_NOTICE_COPY } from "./TextViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

jest.mock("react-syntax-highlighter", () => ({
    Light: Object.assign(({ children }: any) => <pre data-testid="highlighted">{children}</pre>, {
        registerLanguage: jest.fn(),
    }),
}));
jest.mock("react-syntax-highlighter/dist/esm/styles/hljs", () => ({ docco: {}, vs2015: {} }));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/json", () => ({}));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/xml", () => ({}));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/plaintext", () => ({}));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/htmlbars", () => ({}));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/yaml", () => ({}));
jest.mock("react-syntax-highlighter/dist/esm/languages/hljs/ini", () => ({}));

const FILE_URL = "https://s3.example/asset-1/README";

/** Serve `bytes` as S3 does for `Range: bytes=0-8191`: a 206 with the object's total size. */
const serve = (bytes: Uint8Array) => {
    global.fetch = jest.fn(async (_url: string, init: any) => {
        const ranged = !!init?.headers?.Range;
        const body = ranged ? bytes.slice(0, 8192) : bytes;
        return {
            ok: true,
            status: ranged ? 206 : 200,
            headers: {
                get: (name: string) =>
                    ranged && name === "Content-Range"
                        ? `bytes 0-${body.length - 1}/${bytes.length}`
                        : null,
            },
            arrayBuffer: async () => body.slice().buffer,
        };
    }) as any;
};

const renderViewer = (assetKey = "README") =>
    render(
        <TextViewerComponent
            assetId="asset-1"
            databaseId="db-1"
            assetKey={assetKey}
            viewerMode="wide"
            onViewerModeChange={() => undefined}
        />
    );

beforeEach(() => {
    jest.clearAllMocks();
    mockDownloadAsset.mockResolvedValue([true, FILE_URL]);
});

describe("TextViewerComponent", () => {
    it("renders an extensionless text file from a single ranged request", async () => {
        serve(new TextEncoder().encode("Linux kernel\n============\n"));

        renderViewer("README");

        await waitFor(() => expect(screen.getByTestId("highlighted")).toBeInTheDocument());
        expect(screen.getByTestId("highlighted").textContent).toBe("Linux kernel\n============\n");
        expect(global.fetch).toHaveBeenCalledTimes(1);
        expect((global.fetch as jest.Mock).mock.calls[0][1].headers.Range).toBe("bytes=0-8191");
    });

    it("shows the binary notice for an image under an extensionless name, without a full GET", async () => {
        const png = new Uint8Array(50000);
        png.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0x0d]);
        serve(png);

        renderViewer("binary-noext");

        await waitFor(() =>
            expect(screen.getByTestId("text-viewer-binary-notice")).toBeInTheDocument()
        );
        expect(screen.getByText(TEXT_NOTICE_COPY.binary.header)).toBeInTheDocument();
        expect(screen.getByText(TEXT_NOTICE_COPY.binary.body)).toBeInTheDocument();
        expect(screen.queryByTestId("highlighted")).toBeNull();
        expect(global.fetch).toHaveBeenCalledTimes(1);
    });

    it("decodes UTF-16 with a byte-order mark instead of showing NUL-ridden text", async () => {
        const text = "Apache License\nVersion 2.0";
        const bytes = new Uint8Array(2 + text.length * 2);
        bytes.set([0xff, 0xfe]);
        for (let i = 0; i < text.length; i++) bytes[2 + i * 2] = text.charCodeAt(i);
        serve(bytes);

        renderViewer("LICENSE");

        await waitFor(() => expect(screen.getByTestId("highlighted")).toBeInTheDocument());
        expect(screen.getByTestId("highlighted").textContent).toBe(text);
    });

    it("names an unsupported encoding for UTF-16 without a byte-order mark", async () => {
        const text = "Apache License\nVersion 2.0";
        const bytes = new Uint8Array(text.length * 2);
        for (let i = 0; i < text.length; i++) bytes[i * 2] = text.charCodeAt(i);
        serve(bytes);

        renderViewer("LICENSE");

        await waitFor(() =>
            expect(screen.getByTestId("text-viewer-encoding-notice")).toBeInTheDocument()
        );
        expect(screen.getByText(TEXT_NOTICE_COPY.encoding.body)).toBeInTheDocument();
        expect(screen.queryByTestId("highlighted")).toBeNull();
    });

    it("loads a text file over 8 KiB with one more full GET and shows all of it", async () => {
        const text = "line\n".repeat(4000); // 20,000 bytes
        serve(new TextEncoder().encode(text));

        renderViewer("MAINTAINERS");

        await waitFor(() => expect(screen.getByTestId("highlighted")).toBeInTheDocument());
        expect(screen.getByTestId("highlighted").textContent).toBe(text);
        expect(global.fetch).toHaveBeenCalledTimes(2);
    });

    it("still reports a refused download as an error", async () => {
        global.fetch = jest.fn(async () => ({
            ok: false,
            status: 403,
            headers: { get: () => null },
        })) as any;
        const errors = jest.spyOn(console, "error").mockImplementation(() => undefined);

        renderViewer("README");

        await waitFor(() => expect(screen.getByText("Error loading file")).toBeInTheDocument());
        expect(screen.getByText("HTTP error! status: 403")).toBeInTheDocument();
        errors.mockRestore();
    });
});
