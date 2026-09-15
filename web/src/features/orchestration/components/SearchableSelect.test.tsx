/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import SearchableSelect, { QUERY_REPORT_DEBOUNCE_MS } from "./SearchableSelect";
import Dialog from "./Dialog";
import { Z } from "./zLayers";

const OPTIONS = [
    { value: "a1", label: "Building model", detail: "a1" },
    { value: "a2", label: "Terrain scan", detail: "a2" },
];

describe("SearchableSelect", () => {
    it("shows the selected option's label", () => {
        render(
            <SearchableSelect options={OPTIONS} value="a2" onChange={jest.fn()} ariaLabel="Asset" />
        );
        expect(screen.getByLabelText("Asset")).toHaveTextContent("Terrain scan");
    });

    it("filters options by the typed query and selects one", () => {
        const onChange = jest.fn();
        render(
            <SearchableSelect options={OPTIONS} value="" onChange={onChange} ariaLabel="Asset" />
        );
        fireEvent.click(screen.getByLabelText("Asset"));
        fireEvent.change(screen.getByPlaceholderText("Type to search…"), {
            target: { value: "terrain" },
        });
        expect(screen.queryByText("Building model")).not.toBeInTheDocument();
        fireEvent.click(screen.getByText("Terrain scan"));
        expect(onChange).toHaveBeenCalledWith("a2");
    });

    it("shows the raw value when it is absent from the options list", () => {
        render(
            <SearchableSelect
                options={OPTIONS}
                value="a9"
                onChange={jest.fn()}
                ariaLabel="Asset"
                placeholder="Search assets…"
            />
        );
        expect(screen.getByLabelText("Asset")).toHaveTextContent("a9");
    });

    it("closes the popup on Escape", () => {
        render(
            <SearchableSelect options={OPTIONS} value="" onChange={jest.fn()} ariaLabel="Asset" />
        );
        const trigger = screen.getByLabelText("Asset");
        fireEvent.click(trigger);
        expect(trigger).toHaveAttribute("aria-expanded", "true");
        fireEvent.keyDown(screen.getByPlaceholderText("Type to search…"), { key: "Escape" });
        expect(trigger).toHaveAttribute("aria-expanded", "false");
        expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    });

    it("walks the option list with the arrow keys", () => {
        render(
            <SearchableSelect options={OPTIONS} value="" onChange={jest.fn()} ariaLabel="Asset" />
        );
        fireEvent.click(screen.getByLabelText("Asset"));
        const search = screen.getByPlaceholderText("Type to search…");
        fireEvent.keyDown(search, { key: "ArrowDown" });
        expect(document.activeElement).toBe(screen.getByText("Building model").closest("button"));
        fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
        expect(document.activeElement).toBe(screen.getByText("Terrain scan").closest("button"));
        fireEvent.keyDown(document.activeElement!, { key: "ArrowUp" });
        expect(document.activeElement).toBe(screen.getByText("Building model").closest("button"));
    });

    it("renders options as direct children of the listbox", () => {
        render(
            <SearchableSelect options={OPTIONS} value="" onChange={jest.fn()} ariaLabel="Asset" />
        );
        fireEvent.click(screen.getByLabelText("Asset"));
        const listbox = screen.getByRole("listbox");
        screen
            .getAllByRole("option")
            .forEach((option) => expect(option.parentElement).toBe(listbox));
    });

    it("renders a leading option first", () => {
        const onChange = jest.fn();
        render(
            <SearchableSelect
                options={OPTIONS}
                value="/"
                onChange={onChange}
                ariaLabel="File"
                leadingOption={{ value: "/", label: "Whole asset (all files)" }}
            />
        );
        fireEvent.click(screen.getByLabelText("File"));
        // Appears both in the trigger (selected) and the open list.
        expect(screen.getAllByText("Whole asset (all files)").length).toBeGreaterThan(0);
    });
});

/**
 * Server-query mode reports the typed term to the caller, which feeds it straight into a TanStack query
 * key — so one report is one search request. Reporting per keystroke made request volume a function of
 * characters typed, with every intermediate response discarded, on the wizard's hottest path.
 */
describe("SearchableSelect server-query reporting", () => {
    beforeEach(() => {
        jest.useFakeTimers();
    });

    afterEach(() => {
        jest.runOnlyPendingTimers();
        jest.useRealTimers();
    });

    const openWithQueryReporting = (onQueryChange: jest.Mock) => {
        render(
            <SearchableSelect
                options={[]}
                value=""
                onChange={jest.fn()}
                ariaLabel="Asset"
                onQueryChange={onQueryChange}
            />
        );
        fireEvent.click(screen.getByLabelText("Asset"));
        return screen.getByPlaceholderText("Type to search, Enter to refresh…");
    };

    it("reports one term for a typing burst rather than one per character", () => {
        const onQueryChange = jest.fn();
        const input = openWithQueryReporting(onQueryChange);

        "pump".split("").forEach((_c, idx) => {
            fireEvent.change(input, { target: { value: "pump".slice(0, idx + 1) } });
            jest.advanceTimersByTime(50);
        });

        // Control: the field itself is not debounced — the text is on screen immediately.
        expect(input).toHaveValue("pump");
        expect(onQueryChange).not.toHaveBeenCalled();

        jest.advanceTimersByTime(QUERY_REPORT_DEBOUNCE_MS);

        expect(onQueryChange).toHaveBeenCalledTimes(1);
        expect(onQueryChange).toHaveBeenCalledWith("pump");
    });

    it("reports immediately on Enter, as the placeholder advertises", () => {
        const onQueryChange = jest.fn();
        const input = openWithQueryReporting(onQueryChange);

        fireEvent.change(input, { target: { value: "pump" } });
        fireEvent.keyDown(input, { key: "Enter" });

        expect(onQueryChange).toHaveBeenCalledTimes(1);
        expect(onQueryChange).toHaveBeenCalledWith("pump");

        // The settled report must not fire a second, identical search behind it.
        jest.advanceTimersByTime(QUERY_REPORT_DEBOUNCE_MS * 2);
        expect(onQueryChange).toHaveBeenCalledTimes(1);
    });

    it("drops a pending report when the picker closes", () => {
        const onQueryChange = jest.fn();
        const input = openWithQueryReporting(onQueryChange);

        fireEvent.change(input, { target: { value: "pump" } });
        fireEvent.keyDown(input, { key: "Escape" });
        jest.advanceTimersByTime(QUERY_REPORT_DEBOUNCE_MS * 2);

        expect(onQueryChange).not.toHaveBeenCalled();
    });

    it("still filters locally per keystroke when the caller resolves nothing", () => {
        // Control: only the REPORT is deferred. With no onQueryChange the component owns the matching,
        // and that must stay immediate.
        render(
            <SearchableSelect options={OPTIONS} value="" onChange={jest.fn()} ariaLabel="Asset" />
        );
        fireEvent.click(screen.getByLabelText("Asset"));
        fireEvent.change(screen.getByPlaceholderText("Type to search…"), {
            target: { value: "terrain" },
        });

        expect(screen.queryByText("Building model")).not.toBeInTheDocument();
        expect(screen.getByText("Terrain scan")).toBeInTheDocument();
    });
});

/**
 * The option panel is portalled, so it is neither clipped by a scrolling dialog body nor narrowed by
 * a one-third-width column. A portal is only acceptable when it is a Radix layer: a Radix Dialog
 * traps focus, and a plain `createPortal` panel loses the search input's focus the moment it opens.
 */
describe("SearchableSelect portalled panel", () => {
    it("renders the panel outside the trigger's own subtree, above the modal layer", () => {
        render(
            <div data-testid="host">
                <SearchableSelect
                    options={OPTIONS}
                    value=""
                    onChange={jest.fn()}
                    ariaLabel="Asset"
                />
            </div>
        );
        fireEvent.click(screen.getByLabelText("Asset"));
        const listbox = screen.getByRole("listbox");
        expect(screen.getByTestId("host")).not.toContainElement(listbox);
        // The positioned Radix content is the listbox's closest ancestor carrying the inline z-index.
        const positioned = listbox.closest("[style*='z-index']") as HTMLElement;
        expect(Number(positioned.style.zIndex)).toBe(Z.tooltip);
        // Module resets (`input`, `.orch-outline`) are scoped under this class, so the panel opts in.
        expect(positioned.className).toContain("orchestration-root");
        expect(positioned.className).not.toMatch(/\bz-50\b/);
    });

    it("keeps focus on its search input when opened inside a Dialog", () => {
        // Radix Dialog's focus trap refocuses the last element inside the dialog whenever focus lands
        // outside it. A Radix Popover is a nested focus scope the dialog defers to; a hand-rolled
        // portal is not, and its input lost focus immediately.
        render(
            <Dialog open onOpenChange={jest.fn()} title="Host">
                <SearchableSelect
                    options={OPTIONS}
                    value=""
                    onChange={jest.fn()}
                    ariaLabel="Asset"
                />
            </Dialog>
        );
        fireEvent.click(screen.getByLabelText("Asset"));
        const search = screen.getByPlaceholderText("Type to search…");
        expect(document.activeElement).toBe(search);
        // And the dialog is still the one and only dialog: the panel is not a second one.
        expect(screen.getAllByRole("dialog")).toHaveLength(1);
    });

    it("mounts inside the dialog panel it was opened from, beside the scrolling body", () => {
        // A Radix Dialog blocks wheel and touch scrolling everywhere outside its own panel while it is
        // open, so a panel portalled to body could not be scrolled by mouse or touch — only the arrow
        // keys reached options past the first screen. Inside the panel the lock treats the list as the
        // dialog's own scrollable region. The body would clip it, so it is the body's sibling, not its
        // descendant. jsdom has no scroll model, so the mount point is what can be asserted here.
        render(
            <Dialog open onOpenChange={jest.fn()} title="Host">
                <div data-testid="host">
                    <SearchableSelect
                        options={OPTIONS}
                        value=""
                        onChange={jest.fn()}
                        ariaLabel="Asset"
                    />
                </div>
            </Dialog>
        );
        fireEvent.click(screen.getByLabelText("Asset"));
        const listbox = screen.getByRole("listbox");
        expect(screen.getByRole("dialog")).toContainElement(listbox);
        const scrollingBody = screen.getByTestId("host").parentElement as HTMLElement;
        // Control: this is the dialog's scrolling region, and the panel is not inside it.
        expect(scrollingBody.className).toContain("overflow-y-auto");
        expect(scrollingBody).not.toContainElement(listbox);
    });

    it("selects an option and closes from inside a Dialog", () => {
        const onChange = jest.fn();
        render(
            <Dialog open onOpenChange={jest.fn()} title="Host">
                <SearchableSelect
                    options={OPTIONS}
                    value=""
                    onChange={onChange}
                    ariaLabel="Asset"
                />
            </Dialog>
        );
        fireEvent.click(screen.getByLabelText("Asset"));
        fireEvent.click(screen.getByRole("option", { name: /Terrain scan/ }));
        expect(onChange).toHaveBeenCalledWith("a2");
        expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
        expect(screen.getByLabelText("Asset")).toHaveAttribute("aria-expanded", "false");
    });
});
