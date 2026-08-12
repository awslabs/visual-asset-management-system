/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import SearchableSelect from "./SearchableSelect";

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
