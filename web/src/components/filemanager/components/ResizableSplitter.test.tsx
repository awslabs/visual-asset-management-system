/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { ResizableSplitter } from "./ResizableSplitter";

const mount = (onWidthChange = jest.fn()) => {
    const utils = render(
        <ResizableSplitter
            leftPanel={<div>left</div>}
            rightPanel={<div>right</div>}
            initialLeftWidth={300}
            minLeftWidth={200}
            maxLeftWidth={600}
            onWidthChange={onWidthChange}
        />
    );
    const separator = screen.getByRole("separator", { name: "Resize panels" });
    const leftPanel = screen.getByText("left").parentElement as HTMLElement;
    return { ...utils, separator, leftPanel, onWidthChange };
};

describe("ResizableSplitter", () => {
    it("is a focusable vertical separator that reports its width and limits", () => {
        const { separator } = mount();
        expect(separator).toHaveAttribute("aria-orientation", "vertical");
        expect(separator).toHaveAttribute("aria-valuenow", "300");
        expect(separator).toHaveAttribute("aria-valuemin", "200");
        expect(separator).toHaveAttribute("aria-valuemax", "600");
        expect(separator).toHaveAttribute("tabindex", "0");
    });

    it("widens and narrows the left panel with the arrow keys", () => {
        const { separator, leftPanel, onWidthChange } = mount();
        separator.focus();

        fireEvent.keyDown(separator, { key: "ArrowRight" });
        expect(Number(separator.getAttribute("aria-valuenow"))).toBeGreaterThan(300);
        expect(parseInt(leftPanel.style.width, 10)).toBeGreaterThan(300);
        expect(onWidthChange).toHaveBeenLastCalledWith(
            Number(separator.getAttribute("aria-valuenow"))
        );

        fireEvent.keyDown(separator, { key: "ArrowLeft" });
        fireEvent.keyDown(separator, { key: "ArrowLeft" });
        expect(Number(separator.getAttribute("aria-valuenow"))).toBeLessThan(300);
    });

    it("jumps to the limits with Home and End", () => {
        const { separator, leftPanel } = mount();
        fireEvent.keyDown(separator, { key: "End" });
        expect(separator).toHaveAttribute("aria-valuenow", "600");
        expect(leftPanel.style.width).toBe("600px");
        fireEvent.keyDown(separator, { key: "Home" });
        expect(separator).toHaveAttribute("aria-valuenow", "200");
        // Already at the limit: a further step stays clamped.
        fireEvent.keyDown(separator, { key: "ArrowLeft" });
        expect(separator).toHaveAttribute("aria-valuenow", "200");
    });

    it("resizes with a pointer drag and reports the width when the drag ends", () => {
        // jsdom has no PointerEvent, so fireEvent would drop clientX; a MouseEvent subclass
        // carries it, as a browser's PointerEvent does.
        const hadPointerEvent = "PointerEvent" in window;
        if (!hadPointerEvent) {
            (window as any).PointerEvent = class extends MouseEvent {
                pointerId: number;
                constructor(type: string, init: any = {}) {
                    super(type, init);
                    this.pointerId = init.pointerId ?? 0;
                }
            };
        }
        try {
            const { separator, leftPanel, onWidthChange } = mount();
            fireEvent.pointerDown(separator, { pointerId: 1, clientX: 300 });
            fireEvent.pointerMove(separator, { pointerId: 1, clientX: 450 });
            expect(leftPanel.style.width).toBe("450px");
            expect(onWidthChange).not.toHaveBeenCalled();
            fireEvent.pointerUp(separator, { pointerId: 1, clientX: 450 });
            expect(onWidthChange).toHaveBeenCalledWith(450);
            // Moves after the drag ends do nothing.
            fireEvent.pointerMove(separator, { pointerId: 1, clientX: 500 });
            expect(leftPanel.style.width).toBe("450px");
        } finally {
            if (!hadPointerEvent) delete (window as any).PointerEvent;
        }
    });
});
