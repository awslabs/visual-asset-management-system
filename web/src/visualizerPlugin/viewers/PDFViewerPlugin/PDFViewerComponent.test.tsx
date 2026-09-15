/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The PDF viewer's chrome must follow the application theme.
 *
 * Its toolbars, document surround and page-number input were styled with literal light greys
 * (`#f5f5f5`, `#e0e0e0`, `#f9f9f9`, `#ccc`) in inline styles. Inline styles win over any stylesheet, so
 * in dark mode everything around the document stayed white while the rest of the page went dark.
 *
 * These cases assert the styles resolve through Cloudscape design tokens — `var(--color-…)` custom
 * properties that Cloudscape re-points when `.awsui-dark-mode` is set — rather than asserting specific
 * colours, which would just restate whichever palette is current. jsdom preserves the `var()` text in
 * the style attribute but never evaluates it, so what is checkable here is that a token reference is
 * what reaches the DOM: a literal hex cannot follow the theme, whatever its value.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

// react-pdf pulls in canvas rendering, which jsdom does not have. The chrome around the document is
// what these cases are about, so the document itself is stubbed.
jest.mock("react-pdf", () => ({
    Document: ({ children }: any) => <div data-testid="pdf-document">{children}</div>,
    Page: () => <div data-testid="pdf-page" />,
    pdfjs: { GlobalWorkerOptions: {} },
}));

// The worker-setup module uses `import.meta.url`, which jest's CommonJS transform cannot evaluate.
// Mocking it means the real file is never compiled, which is the whole reason that side effect was
// moved out of the component.
jest.mock("./pdfWorker", () => ({}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const PDFViewerComponent = require("./PDFViewerComponent").default;

const props = {
    assetId: "a1",
    databaseId: "d1",
    assetKey: "docs/deck.pdf",
} as any;

/** Every inline background/border/color declaration the component renders. */
function inlineColorStyles(): string[] {
    const out: string[] = [];
    document.querySelectorAll<HTMLElement>("[style]").forEach((el) => {
        const s = el.getAttribute("style") || "";
        for (const decl of s.split(";")) {
            if (/background|border|(^|\s)color\s*:/.test(decl) && decl.trim()) {
                out.push(decl.trim());
            }
        }
    });
    return out;
}

describe("PDFViewerComponent theming", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockDownloadAsset.mockResolvedValue([
            true,
            "https://s3.example/deck.pdf?X-Amz-Signature=x",
        ]);
    });

    it("styles its chrome with theme tokens, not literal colours", async () => {
        render(<PDFViewerComponent {...props} />);
        await waitFor(() => expect(screen.getByTestId("pdf-document")).toBeInTheDocument());

        const decls = inlineColorStyles();
        // Control: the assertion below is vacuous if the component declares no colours at all.
        expect(decls.length).toBeGreaterThan(0);

        // A design token carries its own hex FALLBACK — `var(--color-x-abc123, #ffffff)` — so the
        // fallbacks must be stripped before looking for literals, or every correctly-tokenised
        // declaration reads as hardcoded and the fix looks like the bug.
        const withoutTokens = (d: string) => d.replace(/var\(--[^)]*\)/g, "TOKEN");
        const literals = decls
            .map(withoutTokens)
            .filter((d) => /#[0-9a-f]{3,6}\b/i.test(d) || /:\s*(red|white)\b/i.test(d));
        // Any entry here is an inline style that cannot follow the theme.
        expect(literals).toEqual([]);
    });

    it("resolves those styles through Cloudscape custom properties", async () => {
        render(<PDFViewerComponent {...props} />);
        await waitFor(() => expect(screen.getByTestId("pdf-document")).toBeInTheDocument());

        const decls = inlineColorStyles();
        const tokenBacked = decls.filter((d) => /var\(--color-/.test(d));
        // The positive half: absence of a hex is not enough — the declarations must point at the
        // tokens dark mode actually re-points.
        expect(tokenBacked.length).toBeGreaterThan(0);
    });

    it("gives the page-number input both a background and a text colour", async () => {
        render(<PDFViewerComponent {...props} />);
        await waitFor(() => expect(screen.getByTestId("pdf-document")).toBeInTheDocument());

        const input = document.querySelector<HTMLInputElement>('input[type="number"]');
        expect(input).not.toBeNull();
        const style = input!.getAttribute("style") || "";
        // A native input keeps the browser's own light background and dark text regardless of the page
        // theme, so styling only its border leaves a white box in dark mode.
        expect(style).toMatch(/background-color:\s*var\(--color-/);
        expect(style).toMatch(/(^|;)\s*color:\s*var\(--color-/);
    });
});
