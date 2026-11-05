/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** The result-count badge marks a minimum count with "+". */
import React from "react";
import { render, screen } from "@testing-library/react";
import SearchTopBar from "./SearchTopBar";

function renderBar(props: Partial<React.ComponentProps<typeof SearchTopBar>>) {
    return render(
        <SearchTopBar
            query=""
            onQueryChange={jest.fn()}
            onSearch={jest.fn()}
            onClearAll={jest.fn()}
            {...props}
        />
    );
}

describe("SearchTopBar result count", () => {
    it("shows an exact count as is", () => {
        renderBar({ resultCount: 4 });
        expect(screen.getByText("4 results")).toBeInTheDocument();
    });

    it("shows a minimum with +", () => {
        renderBar({ resultCount: 1200, resultCountIsLowerBound: true });
        expect(screen.getByText(`${(1200).toLocaleString()}+ results`)).toBeInTheDocument();
    });
});
