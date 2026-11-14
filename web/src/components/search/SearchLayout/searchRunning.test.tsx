/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * While a search runs the Search page's top bar keeps keyboard focus: the query box turns
 * read-only, Clear All Filters stays disabled with a reason (which Cloudscape renders as
 * `aria-disabled`), and neither is `disabled`, which would hand focus to the page body. Input
 * while the search runs is ignored.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchTopBar from "./SearchTopBar";
import { SEARCH_RUNNING } from "./searchRunning";

/** The element that names a disabled-with-reason control's reason. */
const describedBy = (element: HTMLElement) =>
    (element.getAttribute("aria-describedby") || "")
        .split(" ")
        .map((id) => document.getElementById(id)?.textContent || "")
        .join(" ");

describe("SearchTopBar while a search runs", () => {
    const TopBar: React.FC<{ loading: boolean; onSearch: () => void; onClearAll?: () => void }> = ({
        loading,
        onSearch,
        onClearAll = jest.fn(),
    }) => (
        <SearchTopBar
            query="pump"
            onQueryChange={jest.fn()}
            onSearch={onSearch}
            onClearAll={onClearAll}
            loading={loading}
            hasActiveFilters
        />
    );

    it("keeps focus in the query box, read-only, and sends nothing on a second Enter", async () => {
        const user = userEvent.setup({ delay: null });
        const onSearch = jest.fn();
        const { rerender } = render(<TopBar loading={false} onSearch={onSearch} />);
        const box = screen.getByPlaceholderText("Search by keywords (wildcard)...");
        box.focus();
        await user.keyboard("{Enter}");
        expect(onSearch).toHaveBeenCalledTimes(1);

        rerender(<TopBar loading onSearch={onSearch} />);
        expect(box).toHaveFocus();
        expect(box).not.toBeDisabled();
        expect(box).toHaveAttribute("readonly");
        await user.keyboard("{Enter}");
        expect(onSearch).toHaveBeenCalledTimes(1);

        // Answered: still focused and editable; Enter searches again.
        rerender(<TopBar loading={false} onSearch={onSearch} />);
        expect(box).toHaveFocus();
        expect(box).not.toHaveAttribute("readonly");
        await user.keyboard("{Enter}");
        expect(onSearch).toHaveBeenCalledTimes(2);
    });

    it("keeps Search and Clear All Filters focusable, says a search is running and ignores a press", async () => {
        const user = userEvent.setup({ delay: null });
        const onSearch = jest.fn();
        const onClearAll = jest.fn();
        const { rerender } = render(
            <TopBar loading={false} onSearch={onSearch} onClearAll={onClearAll} />
        );
        const clear = screen.getByRole("button", { name: "Clear All Filters" });
        clear.focus();
        await user.keyboard("{Enter}");
        expect(onClearAll).toHaveBeenCalledTimes(1);

        rerender(<TopBar loading onSearch={onSearch} onClearAll={onClearAll} />);
        expect(clear).toHaveFocus();
        expect(clear).not.toBeDisabled();
        expect(clear).toHaveAttribute("aria-disabled", "true");
        expect(describedBy(clear)).toContain(SEARCH_RUNNING);
        await user.keyboard("{Enter}");
        expect(onClearAll).toHaveBeenCalledTimes(1);

        const search = screen.getByRole("button", { name: /Search/ });
        expect(search).not.toBeDisabled();
        expect(search).toHaveAttribute("aria-disabled", "true");
        // Cloudscape's loading text, beside the button, announced through its live region.
        expect(search.parentElement).toHaveTextContent(SEARCH_RUNNING);
        await user.click(search);
        expect(onSearch).not.toHaveBeenCalled();
    });
});
