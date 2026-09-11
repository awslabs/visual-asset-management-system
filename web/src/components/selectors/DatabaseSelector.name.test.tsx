/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shared database picker's accessible name.
 *
 * `selectedAriaLabel="Selected"` names the SELECTED-STATE announcement, not the control, so a caller
 * that wrapped this in nothing rendered a combobox a screen reader announces with no name and an empty
 * trigger — WCAG 4.1.2 / 3.3.2. `DatabaseSelectionRequired` is the page's ONLY interactive element
 * until a database is chosen (the gate on /#/auth/tags and /#/metadataschema), so there was nothing
 * else to orient by.
 *
 * The name is supplied by `DatabaseSelector` itself rather than at each call site, which is what makes
 * `DatabaseSelectorWithModal` correct too without touching it.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import DatabaseSelector from "./DatabaseSelector";
import DatabaseSelectionRequired from "./DatabaseSelectionRequired";
import Synonyms from "../../synonyms";

jest.mock("../../services/APIService", () => ({
    fetchAllDatabases: jest.fn(),
}));

const { fetchAllDatabases } = jest.requireMock("../../services/APIService");

const DEFAULT_NAME = `Select ${Synonyms.Database}`;
const DEFAULT_PLACEHOLDER = `Choose a ${Synonyms.database}`;

/**
 * Whether any element's COMPUTED accessible name contains `label`.
 *
 * Not a `[aria-label="..."]` query: this Cloudscape version does not put `ariaLabel` on the control at
 * all. It renders a hidden span holding the text and points the trigger's `aria-labelledby` at it
 * (alongside the trigger content), so the attribute query finds nothing while the control is in fact
 * correctly named. Resolving `aria-labelledby` is what a screen reader does, so asserting on the
 * resolved name tests the behaviour rather than the mechanism — and keeps passing if Cloudscape
 * switches between the two.
 */
const hasAccessibleName = (container: HTMLElement, label: string): boolean =>
    Array.from(container.querySelectorAll("[aria-label],[aria-labelledby]")).some((el) => {
        const direct = el.getAttribute("aria-label") ?? "";
        const resolved = (el.getAttribute("aria-labelledby") ?? "")
            .split(/\s+/)
            .filter(Boolean)
            .map((id) => container.querySelector(`#${CSS.escape(id)}`)?.textContent ?? "")
            .join(" ");
        return `${direct} ${resolved}`.includes(label);
    });

beforeEach(() => {
    jest.clearAllMocks();
    fetchAllDatabases.mockResolvedValue([{ databaseId: "factory-db" }]);
});

describe("DatabaseSelector accessible name", () => {
    it("names the control and shows a placeholder with no caller involvement", async () => {
        const { container } = render(<DatabaseSelector onChange={jest.fn()} />);
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());

        expect(hasAccessibleName(container, DEFAULT_NAME)).toBe(true);
        expect(screen.getByText(DEFAULT_PLACEHOLDER)).toBeInTheDocument();
    });

    it("lets a caller override the name and the placeholder", async () => {
        // Positive control that the values above are DEFAULTS: they are declared before the prop
        // spread, so a call site with its own wording still wins. Were they placed after the spread
        // they would silently override every caller, including AssetUpload's FormField label.
        const { container } = render(
            <DatabaseSelector
                onChange={jest.fn()}
                ariaLabel="Target collection"
                placeholder="Pick a collection"
            />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());

        expect(hasAccessibleName(container, "Target collection")).toBe(true);
        expect(hasAccessibleName(container, DEFAULT_NAME)).toBe(false);
        expect(screen.getByText("Pick a collection")).toBeInTheDocument();
    });
});

describe("DatabaseSelectionRequired gate", () => {
    it("presents a named, labelled, non-empty picker", async () => {
        const { container } = render(
            <DatabaseSelectionRequired
                title="Tags"
                description="Tags are managed per database."
                onSelect={jest.fn()}
            />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());

        // A programmatically associated label, from the FormField wrapper...
        expect(screen.getByText(Synonyms.Database)).toBeInTheDocument();
        // ...the control's own name, from DatabaseSelector...
        expect(hasAccessibleName(container, DEFAULT_NAME)).toBe(true);
        // ...and a trigger that says what it is for rather than rendering blank.
        expect(screen.getByText(DEFAULT_PLACEHOLDER)).toBeInTheDocument();
    });
});
