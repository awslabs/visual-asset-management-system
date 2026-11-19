/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The card view's header counter shows a minimum with "+", as the table header and the badge do: every
 * natural-language count that is not zero is one, and so is a keyword total some permission rules
 * cannot count in advance.
 */
import React from "react";
import { render } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import CardView from "./CardView";

jest.mock("../SearchPreviewThumbnail/PreviewThumbnailCell", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../SearchPreviewThumbnail/FilePreviewThumbnailCell", () => ({
    __esModule: true,
    default: () => null,
}));

const renderCards = (totalItems: number, totalItemsIsLowerBound?: boolean) =>
    render(
        <CardView
            items={[]}
            selectedItems={[]}
            onSelectionChange={jest.fn()}
            loading={false}
            cardSize="medium"
            showThumbnails={false}
            recordType="file"
            onOpenPreview={jest.fn()}
            currentPageIndex={1}
            pagesCount={1}
            onPageChange={jest.fn()}
            totalItems={totalItems}
            totalItemsIsLowerBound={totalItemsIsLowerBound}
        />
    );

const counter = (container: HTMLElement) =>
    createWrapper(container)
        .findCards()
        ?.findHeader()
        ?.findHeader()
        ?.findCounter()
        ?.getElement()
        .textContent?.trim();

describe("CardView count", () => {
    it("shows a minimum with +", () => {
        const { container } = renderCards(2, true);
        expect(counter(container)).toBe("(2+)");
    });

    it("shows an exact count as it is", () => {
        expect(counter(renderCards(2, false).container)).toBe("(2)");
        expect(counter(renderCards(2).container)).toBe("(2)");
    });
});
