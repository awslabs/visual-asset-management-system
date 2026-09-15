/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * This viewer frames a document a user uploaded, so the sandbox is the whole
 * security boundary: `allow-scripts` together with `allow-same-origin` hands the
 * framed markup its real origin instead of an opaque one, which is exactly what
 * lets it reach that origin's storage. The URL it frames is a presigned S3 GET
 * carrying a signature and session token, so it must not reach the console either.
 *
 * It also must not frame the storage URL directly. VAMS stores asset files with a generic content
 * type — an uploaded `.html` arrives as `binary/octet-stream` — and a frame served that type renders
 * nothing: no error event, no CSP violation, no console output, just an empty panel. The viewer
 * therefore fetches the bytes and frames them as a Blob it types itself.
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import HTMLViewerComponent from "./HTMLViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

const PRESIGNED =
    "https://s3.us-east-1.amazonaws.com/bucket/deck.html?X-Amz-Signature=abc123" +
    "&X-Amz-Security-Token=tok";
const MARKUP = "<html><body><h1>Deck</h1></body></html>";
const BLOB_URL = "blob:https://vams.example/9f1c-html";

const props = {
    assetId: "a1",
    databaseId: "d1",
    assetKey: "docs/deck.html",
} as any;

const iframe = () => document.querySelector("iframe");

/** Blobs handed to createObjectURL, so the declared type can be asserted. */
let created: Blob[] = [];
let revoked: string[] = [];

beforeEach(() => {
    jest.clearAllMocks();
    created = [];
    revoked = [];
    mockDownloadAsset.mockResolvedValue([true, PRESIGNED]);
    // The storage object is deliberately served as binary/octet-stream here — the condition the fix
    // exists for. What the viewer frames must not depend on it.
    global.fetch = jest.fn().mockResolvedValue({
        ok: true,
        status: 200,
        text: async () => MARKUP,
        headers: { get: () => "binary/octet-stream" },
    }) as any;
    (URL as any).createObjectURL = jest.fn((blob: Blob) => {
        created.push(blob);
        return BLOB_URL;
    });
    (URL as any).revokeObjectURL = jest.fn((url: string) => revoked.push(url));
});

describe("HTMLViewerComponent", () => {
    it("frames the document without allow-same-origin or allow-popups", async () => {
        render(<HTMLViewerComponent {...props} />);

        await waitFor(() => expect(iframe()).not.toBeNull());
        const sandbox = iframe()?.getAttribute("sandbox");
        expect(sandbox).toBe("allow-scripts allow-forms");
        expect(sandbox).not.toContain("allow-same-origin");
        expect(sandbox).not.toContain("allow-popups");
        expect(iframe()?.getAttribute("referrerpolicy")).toBe("no-referrer");
    });

    it("recognises a sandbox that grants same-origin", () => {
        // Positive control for the assertions above: they are written against the
        // attribute string, so prove the same checks fail on the old value.
        const old = "allow-scripts allow-same-origin allow-forms allow-popups";
        expect(old).toContain("allow-same-origin");
        expect(old).not.toBe("allow-scripts allow-forms");
    });

    it("frames the markup as text/html rather than the storage URL", async () => {
        render(<HTMLViewerComponent {...props} />);
        await waitFor(() => expect(iframe()).not.toBeNull());

        // The fix: the bytes are fetched and re-typed. Framing the presigned URL directly is what
        // produced a blank panel, because the object's own type is binary/octet-stream.
        expect(global.fetch).toHaveBeenCalledWith(PRESIGNED);
        expect(created).toHaveLength(1);
        expect(created[0].type).toBe("text/html");
        expect(iframe()?.getAttribute("src")).toBe(BLOB_URL);
    });

    it("keeps the presigned URL out of the console and out of the DOM", async () => {
        const log = jest.spyOn(console, "log").mockImplementation(() => {});

        render(<HTMLViewerComponent {...props} />);
        await waitFor(() => expect(iframe()).not.toBeNull());

        const logged = log.mock.calls.map((args) => args.map(String).join(" ")).join("\n");
        // The success line still has to exist — otherwise this asserts nothing.
        expect(logged).toContain("docs/deck.html");
        expect(logged).not.toMatch(/X-Amz-Signature|X-Amz-Security-Token/);
        // The signed URL used to be the iframe's src, which put a bearer credential in the DOM and
        // in any access log that recorded the frame request.
        expect(document.body.innerHTML).not.toMatch(/X-Amz-Signature|X-Amz-Security-Token/);
        log.mockRestore();
    });

    it("releases the blob URL when the viewer goes away", async () => {
        const { unmount } = render(<HTMLViewerComponent {...props} />);
        await waitFor(() => expect(iframe()).not.toBeNull());

        unmount();
        // Without this every document opened in a session would leak one blob URL, holding its bytes
        // for the lifetime of the page.
        expect(revoked).toContain(BLOB_URL);
    });

    it("reports a storage error instead of framing nothing", async () => {
        global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 403 }) as any;

        render(<HTMLViewerComponent {...props} />);

        await waitFor(() => expect(screen.getByText(/403/)).toBeInTheDocument());
        expect(iframe()).toBeNull();
    });

    it("reports a frame blocked by content security policy instead of a blank panel", async () => {
        render(<HTMLViewerComponent {...props} />);
        await waitFor(() => expect(iframe()).not.toBeNull());

        // A CSP-refused frame fires no error on the element; the document-level
        // violation event is the only signal. A blocked blob: URL is reported as the bare scheme,
        // with no origin to compare against, so the handler has to match on the scheme.
        const violation: any = new Event("securitypolicyviolation");
        violation.violatedDirective = "frame-src";
        violation.blockedURI = "blob";
        act(() => {
            document.dispatchEvent(violation);
        });

        await waitFor(() =>
            expect(
                screen.getByText(/blocked by the site's content security policy/i)
            ).toBeInTheDocument()
        );
    });

    it("ignores a violation from another directive or another origin", async () => {
        // Positive control for the test above: the handler must be selective, or
        // any CSP report on the page would blank out the viewer.
        render(<HTMLViewerComponent {...props} />);
        await waitFor(() => expect(iframe()).not.toBeNull());

        const wrongDirective: any = new Event("securitypolicyviolation");
        wrongDirective.violatedDirective = "img-src";
        wrongDirective.blockedURI = "blob";
        const wrongOrigin: any = new Event("securitypolicyviolation");
        wrongOrigin.violatedDirective = "frame-src";
        wrongOrigin.blockedURI = "https://elsewhere.example/thing.html";
        // act() so a state update from a mishandled event would be flushed and
        // fail the assertions below rather than arriving after them.
        act(() => {
            document.dispatchEvent(wrongDirective);
            document.dispatchEvent(wrongOrigin);
        });

        expect(screen.queryByText(/content security policy/i)).toBeNull();
        expect(iframe()).not.toBeNull();
    });
});
