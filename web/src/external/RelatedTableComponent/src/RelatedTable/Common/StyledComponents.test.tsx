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
            //TODO: Make assertions
            //
            // expect(container).toMatchInlineSnapshot(`
            //     <div>
            //       <div
            //         class="sc-gueYoa hfiXMU"
            //       />
            //     </div>
            // `);
        });
    });

    describe("Wrapper", () => {
        it("renders correctly", () => {
            const { container } = render(<Wrapper height={1} />);
            //TODO: Make assertions
            //
            // expect(container).toMatchInlineSnapshot(`
            //     <div>
            //       <div
            //         class="sc-beqWaB hZsRqR"
            //         height="1"
            //       />
            //     </div>
            // `);
        });
    });
});
