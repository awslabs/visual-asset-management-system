/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import SearchInput from "./SearchInput";

describe("SearchInput", () => {
    it("shows the placeholder it is given", () => {
        render(<SearchInput value="" onChange={jest.fn()} placeholder="Search pipelines" />);
        expect(screen.getByRole("textbox", { name: "Search" })).toHaveAttribute(
            "placeholder",
            "Search pipelines"
        );
    });

    it("has no placeholder when none is given", () => {
        render(<SearchInput value="" onChange={jest.fn()} />);
        expect(screen.getByRole("textbox", { name: "Search" })).not.toHaveAttribute("placeholder");
    });
});
