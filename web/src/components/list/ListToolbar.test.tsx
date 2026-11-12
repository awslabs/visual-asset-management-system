/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The list toolbar's layout, as TableList's filter slot lays it out: the search on the left with
 * the refresh button right next to it, then the filters, aligned right, in a group of their own.
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import ListToolbar from "./ListToolbar";

const groups = (container: HTMLElement) =>
    Array.from(
        container.querySelectorAll<HTMLElement>("[data-list-toolbar] > [data-list-toolbar-group]")
    ).map((group) => group.getAttribute("data-list-toolbar-group"));

describe("ListToolbar", () => {
    it("puts the search, then the refresh beside it, then the filters group", () => {
        const onRefresh = jest.fn();
        const { container } = render(
            <ListToolbar
                search={<input aria-label="Search things" />}
                onRefresh={onRefresh}
                refreshLabel="Refresh things"
                filters={<select aria-label="Filter things" />}
            />
        );
        expect(groups(container)).toEqual(["search", "filters"]);

        const search = container.querySelector("[data-list-toolbar-group='search']")!;
        const controls = Array.from(search.querySelectorAll("input, button"));
        expect(controls[0]).toBe(screen.getByLabelText("Search things"));
        expect(controls[1]).toBe(screen.getByRole("button", { name: "Refresh things" }));

        const filters = container.querySelector("[data-list-toolbar-group='filters']")!;
        expect(filters.contains(screen.getByLabelText("Filter things"))).toBe(true);

        fireEvent.click(screen.getByRole("button", { name: "Refresh things" }));
        expect(onRefresh).toHaveBeenCalledTimes(1);
    });

    it("lets the search group grow, so the filters sit at the right and wrap below on a narrow screen", () => {
        const { container } = render(
            <ListToolbar search={<input aria-label="Search" />} filters={<span>filters</span>} />
        );
        const toolbar = container.querySelector<HTMLElement>("[data-list-toolbar]")!;
        expect(toolbar.style.display).toBe("flex");
        expect(toolbar.style.flexWrap).toBe("wrap");
        const search = container.querySelector<HTMLElement>("[data-list-toolbar-group='search']")!;
        expect(search.style.flex).toBe("1 1 250px");
    });

    it("keeps the refresh at the left of a toolbar without a search", () => {
        const { container } = render(
            <ListToolbar onRefresh={jest.fn()} filters={<span>filters</span>} />
        );
        const search = container.querySelector("[data-list-toolbar-group='search']")!;
        expect(search.querySelector("button")?.getAttribute("aria-label")).toBe("Refresh data");
        expect(groups(container)).toEqual(["search", "filters"]);
    });

    it("renders no refresh button and no filters group when it has neither", () => {
        const { container } = render(<ListToolbar search={<input aria-label="Search" />} />);
        expect(screen.queryByRole("button")).toBeNull();
        expect(groups(container)).toEqual(["search"]);
    });
});
