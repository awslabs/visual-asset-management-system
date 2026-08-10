/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A template's instructions are where a pipeline documents every metadata key it reads, so they are
 * both essential and long. Two failure modes are equally bad: hiding them (the wizard previously
 * never displayed them at all, so guidance written for the operator was only visible in the template
 * EDITOR), and dumping 26 lines inline so the form they explain is pushed off screen.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import InstructionsPanel, { fitsInline } from "./InstructionsPanel";

const SHORT = "Select the source model as the input file.";
const LONG = Array.from({ length: 20 }, (_, i) => `KEY_${i}  does a thing`).join("\n");

describe("InstructionsPanel", () => {
    it("renders short instructions inline so they need no interaction", () => {
        render(<InstructionsPanel text={SHORT} />);
        expect(screen.getByTestId("instructions-inline")).toBeInTheDocument();
        expect(screen.getByText(SHORT)).toBeInTheDocument();
        expect(screen.queryByTestId("instructions-tooltip-trigger")).not.toBeInTheDocument();
    });

    it("collapses long instructions behind a trigger instead of filling the screen", () => {
        render(<InstructionsPanel text={LONG} />);
        expect(screen.getByTestId("instructions-tooltip-trigger")).toBeInTheDocument();
        expect(screen.queryByTestId("instructions-inline")).not.toBeInTheDocument();
    });

    it("says how much is hidden so the trigger is worth clicking", () => {
        render(<InstructionsPanel text={LONG} />);
        expect(screen.getByTestId("instructions-tooltip-trigger")).toHaveTextContent("(20 lines)");
    });

    it("exposes the hidden content to assistive tech via the trigger label", () => {
        // Hover-only content is unreachable by keyboard and screen reader; the trigger is a real
        // focusable button and names what it reveals.
        render(<InstructionsPanel text={LONG} title="Instructions for this template" />);
        expect(
            screen.getByRole("button", { name: /Instructions for this template \(20 lines\)/ })
        ).toBeInTheDocument();
    });

    it("renders nothing at all when there are no instructions", () => {
        // An empty labelled box reads as "there is guidance and it is blank".
        const { container } = render(<InstructionsPanel text="" />);
        expect(container).toBeEmptyDOMElement();
    });

    it("renders nothing for whitespace-only instructions", () => {
        const { container } = render(<InstructionsPanel text={"  \n\n  "} />);
        expect(container).toBeEmptyDOMElement();
    });

    it("renders nothing when the field is absent", () => {
        const { container } = render(<InstructionsPanel />);
        expect(container).toBeEmptyDOMElement();
    });

    it("preserves line breaks rather than collapsing them", () => {
        // Metadata documentation is a key-per-line list; collapsed whitespace makes it unreadable.
        const text = "Line one\nLine two\nLine three";
        render(<InstructionsPanel text={text} />);
        const el = screen.getByTestId("instructions-inline");
        expect(el.textContent).toContain("Line one");
        // The rendered node uses pre-wrap so the newlines survive to the DOM.
        expect(el.innerHTML).toContain("whitespace-pre-wrap");
    });

    it("shows the title so the panel is identifiable", () => {
        render(<InstructionsPanel text={SHORT} title="Instructions for this template" />);
        expect(screen.getByText("Instructions for this template")).toBeInTheDocument();
    });
});

describe("fitsInline", () => {
    it("treats a short single line as inline", () => {
        expect(fitsInline("hello", 6, 400)).toBe(true);
    });

    it("sends too many lines to the tooltip", () => {
        expect(fitsInline("a\nb\nc\nd\ne\nf\ng", 6, 400)).toBe(false);
    });

    it("sends too many characters to the tooltip even on few lines", () => {
        // A handful of very long lines fills the surface just as effectively as many short ones,
        // which a line-count-only rule would miss.
        expect(fitsInline("x".repeat(500), 6, 400)).toBe(false);
    });

    it("treats empty text as inline (the caller renders nothing anyway)", () => {
        expect(fitsInline("", 6, 400)).toBe(true);
    });

    it("is inclusive at the limits", () => {
        expect(fitsInline("a\nb\nc", 3, 10)).toBe(true);
        expect(fitsInline("a\nb\nc\nd", 3, 10)).toBe(false);
    });
});
