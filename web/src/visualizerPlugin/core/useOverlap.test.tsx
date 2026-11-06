/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { boxesOverlap, useInfoBoxClearOfPanel, useOverlap } from "./useOverlap";

type Box = { left: number; top: number; right: number; bottom: number };

// What getBoundingClientRect returns for each element marked with data-box; jsdom has no layout
let layout: Record<string, Box> = {};
// The getBoundingClientRect spy: every measurement reads both elements' rectangles through it
let rects: jest.SpyInstance;
const asRect = ({ left, top, right, bottom }: Box) =>
    ({
        left,
        top,
        right,
        bottom,
        x: left,
        y: top,
        width: right - left,
        height: bottom - top,
    } as DOMRect);

// A 414 px wide viewer: the panel down its left side, the info box at its top right
const PANEL: Box = { left: 10, top: 20, right: 330, bottom: 876 };
const INFO_OVER_PANEL: Box = { left: 164, top: 10, right: 404, bottom: 144 };
const INFO_BESIDE_PANEL: Box = { left: 1350, top: 10, right: 1590, bottom: 144 };

const Pair: React.FC<{ showFirst?: boolean; showSecond?: boolean }> = ({
    showFirst = true,
    showSecond = true,
}) => {
    const [first, setFirst] = useState<HTMLElement | null>(null);
    const [second, setSecond] = useState<HTMLElement | null>(null);
    const overlap = useOverlap(first, second);
    return (
        <div data-testid="container">
            {showFirst && <div data-box="panel" ref={setFirst} />}
            {showSecond && <div data-box="info" ref={setSecond} />}
            <output>{overlap ? "overlap" : "apart"}</output>
        </div>
    );
};

const Viewer: React.FC<{ showPanel?: boolean }> = ({ showPanel = true }) => {
    const { panelRef, infoBoxRef, infoBoxVisibility } = useInfoBoxClearOfPanel();
    return (
        <div>
            {showPanel && <div data-box="panel" ref={panelRef} />}
            <div data-box="info" ref={infoBoxRef} style={{ visibility: infoBoxVisibility }}>
                Viewer name
            </div>
        </div>
    );
};

const result = () => screen.getByRole("status").textContent;
const box = (name: string) => document.querySelector(`[data-box="${name}"]`) as HTMLElement;

const resizeWindow = () =>
    act(() => {
        fireEvent(window, new Event("resize"));
    });

// jsdom has no ResizeObserver. This stand-in records what each observer watches; resizeElement calls
// back every observer still watching the element, and disconnect stops the watching, as in a browser
type FakeObserver = { callback: () => void; observed: Element[]; disconnect: jest.Mock };
const observers: FakeObserver[] = [];

const resizeElement = (element: Element) =>
    act(() => {
        observers
            .filter((observer) => observer.observed.includes(element))
            .forEach((observer) => observer.callback());
    });

const withFakeResizeObserver = () => {
    const originalResizeObserver = (globalThis as any).ResizeObserver;

    beforeEach(() => {
        observers.length = 0;
        (globalThis as any).ResizeObserver = class {
            callback: () => void;
            observed: Element[] = [];
            disconnect = jest.fn(() => {
                this.observed = [];
            });
            constructor(callback: () => void) {
                this.callback = callback;
                observers.push(this);
            }
            observe(element: Element) {
                this.observed.push(element);
            }
            unobserve = jest.fn();
        };
    });

    afterEach(() => {
        (globalThis as any).ResizeObserver = originalResizeObserver;
    });
};

describe("boxesOverlap", () => {
    it("is true for boxes that share some area, one inside the other included", () => {
        expect(boxesOverlap(PANEL, INFO_OVER_PANEL)).toBe(true);
        expect(boxesOverlap(PANEL, { left: 20, top: 30, right: 40, bottom: 50 })).toBe(true);
    });

    it("is false for boxes apart, boxes that only touch, and boxes with no area", () => {
        expect(boxesOverlap(PANEL, INFO_BESIDE_PANEL)).toBe(false);
        expect(boxesOverlap(PANEL, { left: 330, top: 20, right: 400, bottom: 80 })).toBe(false);
        expect(boxesOverlap(PANEL, { left: 20, top: 876, right: 80, bottom: 900 })).toBe(false);
        const empty = { left: 0, top: 0, right: 0, bottom: 0 };
        expect(boxesOverlap(empty, empty)).toBe(false);
    });
});

describe("useOverlap", () => {
    beforeEach(() => {
        layout = { panel: PANEL, info: INFO_OVER_PANEL };
        rects = jest
            .spyOn(Element.prototype, "getBoundingClientRect")
            .mockImplementation(function (this: Element) {
                const box = layout[(this as HTMLElement).dataset?.box ?? ""];
                return asRect(box ?? { left: 0, top: 0, right: 0, bottom: 0 });
            });
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("is true while the two elements overlap on screen and false when they do not", () => {
        const { unmount } = render(<Pair />);
        expect(result()).toBe("overlap");
        unmount();

        layout.info = INFO_BESIDE_PANEL;
        render(<Pair />);
        expect(result()).toBe("apart");
    });

    it("is false while either element is not mounted", () => {
        const { rerender } = render(<Pair showSecond={false} />);
        expect(result()).toBe("apart");

        rerender(<Pair />);
        expect(result()).toBe("overlap");

        rerender(<Pair showFirst={false} />);
        expect(result()).toBe("apart");
    });

    it("measures again after the caller renders", () => {
        const { rerender } = render(<Pair />);
        expect(result()).toBe("overlap");

        layout.info = INFO_BESIDE_PANEL;
        rerender(<Pair />);
        expect(result()).toBe("apart");
    });

    it("measures again when the window is resized", () => {
        layout.info = INFO_BESIDE_PANEL;
        render(<Pair />);
        expect(result()).toBe("apart");

        // The window narrows: the info box, anchored right, moves over the panel
        layout.info = INFO_OVER_PANEL;
        resizeWindow();
        expect(result()).toBe("overlap");
    });

    it("stops listening to window resizes once unmounted or once either element unmounts", () => {
        const { unmount } = render(<Pair />);
        unmount();
        rects.mockClear();
        resizeWindow();
        expect(rects).not.toHaveBeenCalled();

        const { rerender } = render(<Pair />);
        rerender(<Pair showFirst={false} />);
        rects.mockClear();
        resizeWindow();
        expect(rects).not.toHaveBeenCalled();
    });

    describe("with a ResizeObserver", () => {
        withFakeResizeObserver();

        it("watches both elements and what they sit in, and measures again when one resizes", () => {
            layout.info = INFO_BESIDE_PANEL;
            const { unmount } = render(<Pair />);
            expect(result()).toBe("apart");

            expect(observers).toHaveLength(1);
            const [observer] = observers;
            expect(observer.observed).toEqual(
                expect.arrayContaining([box("panel"), box("info"), screen.getByTestId("container")])
            );

            // The info box gains a line and grows to the left, over the panel
            layout.info = INFO_OVER_PANEL;
            resizeElement(box("info"));
            expect(result()).toBe("overlap");

            unmount();
            expect(observer.disconnect).toHaveBeenCalled();
        });

        it("stops watching the elements once unmounted or once either element unmounts", () => {
            const { unmount } = render(<Pair />);
            const info = box("info");
            unmount();
            rects.mockClear();
            resizeElement(info);
            expect(rects).not.toHaveBeenCalled();

            const { rerender } = render(<Pair />);
            const panel = box("panel");
            const secondInfo = box("info");
            rerender(<Pair showFirst={false} />);
            rects.mockClear();
            resizeElement(panel);
            resizeElement(secondInfo);
            expect(rects).not.toHaveBeenCalled();
        });
    });
});

describe("useInfoBoxClearOfPanel", () => {
    withFakeResizeObserver();

    beforeEach(() => {
        layout = { panel: PANEL, info: INFO_OVER_PANEL };
        rects = jest
            .spyOn(Element.prototype, "getBoundingClientRect")
            .mockImplementation(function (this: Element) {
                const box = layout[(this as HTMLElement).dataset?.box ?? ""];
                return asRect(box ?? { left: 0, top: 0, right: 0, bottom: 0 });
            });
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("hides the info box while the open panel overlaps it, and keeps it in the layout", () => {
        render(<Viewer />);
        const info = screen.getByText("Viewer name");
        expect(info).not.toBeVisible();
        expect(info).toBeInTheDocument();
    });

    it("shows the info box when there is room beside the panel", () => {
        layout.info = INFO_BESIDE_PANEL;
        render(<Viewer />);
        expect(screen.getByText("Viewer name")).toBeVisible();
    });

    it("shows the info box while the panel is hidden", () => {
        const { rerender } = render(<Viewer />);
        expect(screen.getByText("Viewer name")).not.toBeVisible();

        rerender(<Viewer showPanel={false} />);
        expect(screen.getByText("Viewer name")).toBeVisible();
    });

    it("stops measuring on window and element resizes once the panel is hidden or the viewer closes", () => {
        const { rerender, unmount } = render(<Viewer />);
        const info = screen.getByText("Viewer name");
        const panel = box("panel");

        rerender(<Viewer showPanel={false} />);
        rects.mockClear();
        resizeWindow();
        resizeElement(panel);
        resizeElement(info);
        expect(rects).not.toHaveBeenCalled();

        rerender(<Viewer />);
        unmount();
        rects.mockClear();
        resizeWindow();
        resizeElement(info);
        expect(rects).not.toHaveBeenCalled();
    });
});
