/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import VirtualList from "./VirtualList";

const ITEMS = Array.from({ length: 1000 }, (_, i) => `row-${i}`);

const renderList = (items = ITEMS, height = 300, rowHeight = 30, overscan = 5) =>
    render(
        <VirtualList
            items={items}
            height={height}
            rowHeight={rowHeight}
            overscan={overscan}
            rowKey={(item) => item}
            renderRow={(item) => <span>{item}</span>}
            aria-label="Rows"
        />
    );

const mounted = () => screen.getAllByRole("listitem").map((li) => li.textContent);

/**
 * The list mounts a window, never the whole set. jsdom lays nothing out, so the window is derived
 * from the `height` and `rowHeight` props alone — which is also what makes it deterministic here.
 */
describe("VirtualList", () => {
    it("mounts only the visible rows plus the overscan for a thousand items", () => {
        renderList();
        const rows = mounted();
        // 300px / 30px = 10 rows in view, plus 5 of overscan below (none above at the top).
        expect(rows).toHaveLength(15);
        expect(rows[0]).toBe("row-0");
        expect(rows[14]).toBe("row-14");
    });

    it("sizes the spacer to the full list so the scrollbar reads the true length", () => {
        renderList();
        const container = screen.getByRole("list");
        expect(container.style.height).toBe("300px");
        const spacer = container.firstElementChild as HTMLElement;
        expect(spacer.style.height).toBe(`${1000 * 30}px`);
    });

    it("moves the window with the scroll offset and places rows at their true offset", () => {
        renderList();
        const container = screen.getByRole("list");
        // Row 500 is at 15000px; scrolled there, the window is rows 495..514 (overscan both sides).
        Object.defineProperty(container, "scrollTop", { value: 15000, writable: true });
        fireEvent.scroll(container);
        const rows = mounted();
        expect(rows[0]).toBe("row-495");
        expect(rows[rows.length - 1]).toBe("row-514");
        expect(rows).toHaveLength(20);
        const first = screen.getByText("row-495").parentElement as HTMLElement;
        expect(first.style.top).toBe(`${495 * 30}px`);
        expect(first.style.height).toBe("30px");
    });

    it("shrinks to its rows when they fit and renders them all", () => {
        renderList(ITEMS.slice(0, 4));
        const container = screen.getByRole("list");
        expect(container.style.height).toBe(`${4 * 30}px`);
        expect(mounted()).toEqual(["row-0", "row-1", "row-2", "row-3"]);
    });

    it("is focusable so the keyboard scrolls it", () => {
        renderList();
        expect(screen.getByRole("list")).toHaveAttribute("tabindex", "0");
    });

    it("pulls the window back when the list shrinks under the scroll offset", () => {
        const { rerender } = renderList();
        const container = screen.getByRole("list");
        Object.defineProperty(container, "scrollTop", { value: 15000, writable: true });
        fireEvent.scroll(container);
        expect(mounted()[0]).toBe("row-495");

        rerender(
            <VirtualList
                items={ITEMS.slice(0, 20)}
                height={300}
                rowHeight={30}
                overscan={5}
                rowKey={(item) => item}
                renderRow={(item) => <span>{item}</span>}
                aria-label="Rows"
            />
        );
        // 20 rows = 600px, viewport 300px, so the furthest offset is 300px: rows 5..19 (+overscan).
        const rows = mounted();
        expect(rows[0]).toBe("row-5");
        expect(rows[rows.length - 1]).toBe("row-19");
    });
});
