/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import StringListInput from "./StringListInput";

describe("StringListInput", () => {
    it("adds an entry via the Add button and renders it", () => {
        const onChange = jest.fn();
        render(<StringListInput value={[]} onChange={onChange} ariaLabel="Add filter" />);
        fireEvent.change(screen.getByLabelText("Add filter"), { target: { value: "*.glb" } });
        fireEvent.click(screen.getByText("Add"));
        expect(onChange).toHaveBeenCalledWith(["*.glb"]);
    });

    it("adds an entry on Enter", () => {
        const onChange = jest.fn();
        render(<StringListInput value={[]} onChange={onChange} ariaLabel="Add filter" />);
        const input = screen.getByLabelText("Add filter");
        fireEvent.change(input, { target: { value: "/models/" } });
        fireEvent.keyDown(input, { key: "Enter" });
        expect(onChange).toHaveBeenCalledWith(["/models/"]);
    });

    it("does not add a duplicate entry", () => {
        const onChange = jest.fn();
        render(<StringListInput value={["*.glb"]} onChange={onChange} ariaLabel="Add filter" />);
        fireEvent.change(screen.getByLabelText("Add filter"), { target: { value: "*.glb" } });
        fireEvent.click(screen.getByText("Add"));
        expect(onChange).not.toHaveBeenCalled();
    });

    it("removes an entry", () => {
        const onChange = jest.fn();
        render(
            <StringListInput
                value={["*.glb", "*.obj"]}
                onChange={onChange}
                ariaLabel="Add filter"
            />
        );
        fireEvent.click(screen.getByLabelText("Remove *.glb"));
        expect(onChange).toHaveBeenCalledWith(["*.obj"]);
    });
});
