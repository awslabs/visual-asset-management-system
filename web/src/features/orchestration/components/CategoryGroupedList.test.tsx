/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CategoryGroupedList from "./CategoryGroupedList";

/** An item owning local state, standing in for a card's uncontrolled actions menu. */
const MarkItem: React.FC<{ name: string }> = ({ name }) => {
    const [marked, setMarked] = React.useState(false);
    return (
        <button onClick={() => setMarked(true)}>
            {marked ? `marked ${name}` : `mark ${name}`}
        </button>
    );
};

const ALPHA = { id: "a", name: "Alpha" };
const BETA = { id: "b", name: "Beta" };

const groupBy = () => "Group";
const renderItem = (item: { name: string }) => <MarkItem name={item.name} />;

describe("CategoryGroupedList", () => {
    it("keeps per-item state with its item when getKey is supplied and the items reorder", async () => {
        const getKey = (item: { id: string }) => item.id;

        const { rerender } = render(
            <CategoryGroupedList
                items={[ALPHA, BETA]}
                groupBy={groupBy}
                renderItem={renderItem}
                getKey={getKey}
            />
        );

        await userEvent.click(screen.getByRole("button", { name: "mark Alpha" }));
        expect(screen.getByRole("button", { name: "marked Alpha" })).toBeInTheDocument();

        rerender(
            <CategoryGroupedList
                items={[BETA, ALPHA]}
                groupBy={groupBy}
                renderItem={renderItem}
                getKey={getKey}
            />
        );

        expect(screen.getByRole("button", { name: "marked Alpha" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "mark Beta" })).toBeInTheDocument();
    });

    it("hands per-item state to whichever item takes the position when getKey is omitted", async () => {
        // The positive control for the assertion above: without a stable key the state stays with the
        // INDEX, which is the defect a caller inherits by omitting getKey.
        const { rerender } = render(
            <CategoryGroupedList items={[ALPHA, BETA]} groupBy={groupBy} renderItem={renderItem} />
        );

        await userEvent.click(screen.getByRole("button", { name: "mark Alpha" }));

        rerender(
            <CategoryGroupedList items={[BETA, ALPHA]} groupBy={groupBy} renderItem={renderItem} />
        );

        expect(screen.getByRole("button", { name: "marked Beta" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "mark Alpha" })).toBeInTheDocument();
    });
});
