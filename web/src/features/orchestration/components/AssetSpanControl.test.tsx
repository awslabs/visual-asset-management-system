/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import AssetSpanControl, { normalizeAssetScope, scopeWithSpan } from "./AssetSpanControl";

describe("normalizeAssetScope", () => {
    it("folds the wholeAsset shorthand into the canonical key", () => {
        expect(normalizeAssetScope({ wholeAsset: true })).toEqual({ wholeAssetAllowed: true });
    });

    it("keeps an explicit canonical key and drops the shorthand", () => {
        expect(normalizeAssetScope({ wholeAsset: true, wholeAssetAllowed: false })).toEqual({
            wholeAssetAllowed: false,
        });
    });
});

describe("scopeWithSpan", () => {
    it("emits no shorthand key alongside the canonical one", () => {
        expect(scopeWithSpan({ wholeAsset: true }, "multiple")).toEqual({
            wholeAssetAllowed: true,
            crossAssetAllowed: true,
            singleAssetOnly: false,
        });
    });
});

describe("AssetSpanControl", () => {
    it("checks the whole-asset box for a scope using the shorthand", () => {
        render(<AssetSpanControl scope={{ wholeAsset: true }} onChange={jest.fn()} />);
        expect(screen.getByLabelText(/Allow selecting a whole asset/)).toBeChecked();
    });

    it("does not carry the shorthand through a change", () => {
        const onChange = jest.fn();
        render(<AssetSpanControl scope={{ wholeAsset: true }} onChange={onChange} />);
        fireEvent.click(screen.getByLabelText(/Allow selecting a folder/));
        expect(onChange).toHaveBeenCalledWith({
            wholeAssetAllowed: true,
            folderAllowed: true,
        });
    });
});
