/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { render } from "@testing-library/react";
import React from "react";
import { LeftPad, Wrapper } from "./StyledComponents";

describe("StyledComponents", () => {
    describe("LeftPad", () => {
        it("renders correctly", () => {
            const { container } = render(<LeftPad length={1} />);
            expect(container).toMatchInlineSnapshot(`
                <div>
                  <div
                    class="sc-gEvEer hLJpaz"
                  />
                </div>
            `);
        });
    });

    describe("Wrapper", () => {
        it("renders correctly", () => {
            const { container } = render(<Wrapper height={1} />);
            expect(container).toMatchInlineSnapshot(`
                <div>
                  <div
                    class="sc-aXZVg jFLCgA"
                    height="1"
                  />
                </div>
            `);
        });
    });
});
