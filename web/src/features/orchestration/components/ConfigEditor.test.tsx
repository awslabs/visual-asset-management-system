/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Reveal-and-select behaviour for the find-in-log target.
 *
 * The original implementation only ran at mount and only called setPosition. Two consequences, both
 * reported from the deployed app: stepping between matches moved nothing (the effect never re-ran on
 * an already-mounted editor), and even the first match was merely scrolled to rather than highlighted,
 * so on a long line the operator could not see which occurrence was current.
 *
 * Monaco does not run under jsdom, so `@monaco-editor/react` is mocked with a component that invokes
 * onMount with a fake editor. That is enough to assert the two things that matter: which range is
 * selected, and that it is re-applied when the target changes.
 */

import React from "react";
import { render, act } from "@testing-library/react";
import ConfigEditor from "./ConfigEditor";

// `mock`-prefixed so Jest's hoisted module factory is allowed to reference it.
const mockEditor = {
    setSelection: jest.fn(),
    revealRangeInCenter: jest.fn(),
};

// The lazy import chain (monacoSetup + @monaco-editor/react) is replaced by a component that calls
// onMount with the fake editor.
//
// onMount fires ONCE, on an empty dep list — deliberately. Monaco calls it a single time for the
// life of the instance, and a mock that re-invoked it on every render would let a mount-only
// implementation masquerade as a working one: re-targeting would appear to work because the mock,
// not the component, was driving it.
jest.mock("./monacoSetup", () => ({}));
jest.mock("@monaco-editor/react", () => {
    const React2 = require("react");
    // A named, capitalised component so the react-hooks lint rule recognises it. It tracks its own
    // "already mounted" flag rather than using an effect, which keeps the once-only semantics
    // explicit and independent of render count.
    const FakeMonaco = ({ onMount, value }: any) => {
        const mounted = React2.useRef(false);
        if (!mounted.current) {
            mounted.current = true;
            onMount?.(mockEditor);
        }
        return React2.createElement("pre", null, value);
    };
    return { __esModule: true, default: FakeMonaco };
});

/** Render and flush the lazy/Suspense boundary so onMount has run. */
async function renderEditor(props: any) {
    let utils: any;
    await act(async () => {
        utils = render(<ConfigEditor value={"a\nbcd\nef"} language="plaintext" {...props} />);
    });
    return utils;
}

describe("ConfigEditor find-in-log target", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("selects the matched range, not just the line", async () => {
        await renderEditor({ startLine: 2, startColumn: 2, selectionLength: 3 });

        expect(mockEditor.setSelection).toHaveBeenCalledWith({
            startLineNumber: 2,
            startColumn: 2,
            endLineNumber: 2,
            endColumn: 5, // column + length — the highlight spans the match
        });
    });

    it("scrolls the selected range into view", async () => {
        await renderEditor({ startLine: 2, startColumn: 2, selectionLength: 3 });
        expect(mockEditor.revealRangeInCenter).toHaveBeenCalled();
    });

    it("re-applies when the target moves, without remounting", async () => {
        const { rerender } = await renderEditor({
            startLine: 2,
            startColumn: 1,
            selectionLength: 2,
        });
        const callsAfterMount = mockEditor.setSelection.mock.calls.length;

        // Stepping to another match: the same editor instance must be re-targeted. The original bug
        // was exactly this — nothing happened after mount.
        await act(async () => {
            rerender(
                <ConfigEditor
                    value={"a\nbcd\nef"}
                    language="plaintext"
                    startLine={3}
                    startColumn={1}
                    selectionLength={2}
                />
            );
        });

        expect(mockEditor.setSelection.mock.calls.length).toBeGreaterThan(callsAfterMount);
        expect(mockEditor.setSelection).toHaveBeenLastCalledWith(
            expect.objectContaining({ startLineNumber: 3 })
        );
    });

    it("collapses to a cursor when no selection length is given", async () => {
        // A caller that knows only the line still gets the cursor moved there.
        await renderEditor({ startLine: 2 });
        expect(mockEditor.setSelection).toHaveBeenCalledWith({
            startLineNumber: 2,
            startColumn: 1,
            endLineNumber: 2,
            endColumn: 1,
        });
    });

    it("does nothing when no target line is given", async () => {
        // The editor is also used for plain config editing, where there is no target.
        await renderEditor({});
        expect(mockEditor.setSelection).not.toHaveBeenCalled();
        expect(mockEditor.revealRangeInCenter).not.toHaveBeenCalled();
    });

    it("defaults a zero or missing column to 1 rather than producing an invalid range", async () => {
        await renderEditor({ startLine: 2, startColumn: 0, selectionLength: 2 });
        expect(mockEditor.setSelection).toHaveBeenCalledWith(
            expect.objectContaining({ startColumn: 1, endColumn: 3 })
        );
    });

    it("survives a Monaco call that throws", async () => {
        // A stale target against newer text must not take the editor down.
        mockEditor.setSelection.mockImplementationOnce(() => {
            throw new Error("out of range");
        });
        const { container } = await renderEditor({ startLine: 999, selectionLength: 2 });
        expect(container).toBeTruthy();
    });
});
