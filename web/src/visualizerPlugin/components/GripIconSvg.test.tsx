/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render } from "@testing-library/react";
import { GRIP_ICON_SVG } from "./GripIconSvg";

const renderIcon = () => {
    const { container } = render(<>{GRIP_ICON_SVG}</>);
    expect(container.children).toHaveLength(1);
    return container.firstElementChild as Element;
};

describe("GRIP_ICON_SVG", () => {
    it("is a decorative 1em svg filled with currentColor", () => {
        const svg = renderIcon();
        expect(svg.tagName.toLowerCase()).toBe("svg");
        expect(svg).toHaveAttribute("aria-hidden", "true");
        expect(svg).toHaveAttribute("focusable", "false");
        expect(svg).toHaveAttribute("width", "1em");
        expect(svg).toHaveAttribute("height", "1em");
        expect(svg.querySelector("g")).toHaveAttribute("fill", "currentColor");
    });

    it("draws two columns of three dots and no U+22EE character", () => {
        const svg = renderIcon();
        expect(svg.textContent).toBe("");
        const dots = Array.from(svg.querySelectorAll("circle"));
        expect(dots).toHaveLength(6);
        expect(new Set(dots.map((dot) => dot.getAttribute("cx"))).size).toBe(2);
        expect(new Set(dots.map((dot) => dot.getAttribute("cy"))).size).toBe(3);
    });
});
