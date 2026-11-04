/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * While a search runs the Search page's controls keep keyboard focus: a field or select turns
 * read-only, a button stays disabled with a reason (which Cloudscape renders as `aria-disabled`),
 * and none of them is `disabled`, which would hand focus to the page body. Input while the search
 * runs is ignored; a control unavailable for another reason stays `disabled`.
 */

import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchTopBar from "./SearchTopBar";
import ModeSelector from "./ModeSelector";
import BasicFiltersPanel from "./BasicFiltersPanel";
import AdvancedFiltersPanel from "./AdvancedFiltersPanel";
import MetadataSearchPanel from "./MetadataSearchPanel";
import GeoFilterPanel from "./GeoFilterPanel";
import PreferencesPanel from "./PreferencesPanel";
import { SEARCH_RUNNING } from "./searchRunning";

jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
}));
// The basic filters load their database and tag options on mount.
jest.mock("../../../services/APIService", () => ({
    fetchAllDatabases: jest.fn(() => Promise.resolve([])),
    fetchTags: jest.fn(() => Promise.resolve([])),
}));

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

describe("ModeSelector while a search runs", () => {
    const Mode: React.FC<{
        disabled: boolean;
        onRecordTypeChange: (t: "asset" | "file") => void;
    }> = ({ disabled, onRecordTypeChange }) => (
        <ModeSelector
            recordType="file"
            onRecordTypeChange={onRecordTypeChange}
            showThumbnails={false}
            onThumbnailToggle={jest.fn()}
            disabled={disabled}
        />
    );

    it("keeps focus on the chosen segment and ignores a change of mode", async () => {
        const user = userEvent.setup({ delay: null });
        const onRecordTypeChange = jest.fn();
        const { rerender } = render(
            <Mode disabled={false} onRecordTypeChange={onRecordTypeChange} />
        );
        const files = screen.getByRole("button", { name: "Files" });
        files.focus();
        rerender(<Mode disabled onRecordTypeChange={onRecordTypeChange} />);
        expect(files).toHaveFocus();
        expect(files).not.toBeDisabled();
        expect(files).toHaveAttribute("aria-disabled", "true");
        await user.click(screen.getByRole("button", { name: "Assets" }));
        expect(onRecordTypeChange).not.toHaveBeenCalled();
        // The thumbnail toggle turns read-only rather than disabled.
        const toggle = screen.getByLabelText("Show preview thumbnails");
        expect(toggle).not.toBeDisabled();
        expect(toggle).toHaveAttribute("aria-disabled", "true");
    });
});

describe("Search filter panels while a search runs", () => {
    it("keeps a basic filter field focused and read-only, and a locked database disabled", async () => {
        const Basic: React.FC<{ loading: boolean }> = ({ loading }) => (
            <BasicFiltersPanel
                filters={{ _rectype: { value: "asset" } } as any}
                onFilterChange={jest.fn()}
                loading={loading}
                databaseLocked
                recordType="asset"
            />
        );
        const { rerender } = render(<Basic loading={false} />);
        // The option lists load from the mocked service; let them settle before the assertions.
        await act(() => Promise.resolve());
        const name = screen.getByPlaceholderText(/name \(e\.g\./);
        name.focus();
        rerender(<Basic loading />);
        expect(name).toHaveFocus();
        expect(name).not.toBeDisabled();
        expect(name).toHaveAttribute("readonly");
        const triggerReading = (text: RegExp) =>
            screen.getAllByRole("button").find((b) => text.test(b.textContent || ""));
        const tags = triggerReading(/All tags/);
        expect(tags).not.toBeDisabled();
        expect(tags).toHaveAttribute("aria-disabled", "true");
        // Locked by the URL: unavailable for another reason, so it stays disabled.
        expect(triggerReading(/All .*databases/i)).toBeDisabled();
    });

    it("keeps an advanced filter checkbox focused and read-only, and ignores a toggle", async () => {
        const user = userEvent.setup({ delay: null });
        const onFilterChange = jest.fn();
        const Advanced: React.FC<{ loading: boolean }> = ({ loading }) => (
            <AdvancedFiltersPanel
                filters={{} as any}
                onFilterChange={onFilterChange}
                loading={loading}
                recordType="file"
            />
        );
        const { rerender } = render(<Advanced loading={false} />);
        const dateFilter = screen.getByLabelText("Date modified filter");
        dateFilter.focus();
        rerender(<Advanced loading />);
        expect(dateFilter).toHaveFocus();
        expect(dateFilter).not.toBeDisabled();
        expect(dateFilter).toHaveAttribute("aria-disabled", "true");
        await user.click(dateFilter);
        expect(onFilterChange).not.toHaveBeenCalled();
    });

    it("keeps a date field focused and ignores a change while a search runs", () => {
        const onFilterChange = jest.fn();
        const Advanced: React.FC<{ loading: boolean }> = ({ loading }) => (
            <AdvancedFiltersPanel
                filters={{ date_lastmodified_filter: { operator: "gte", value: "" } } as any}
                onFilterChange={onFilterChange}
                loading={loading}
                recordType="file"
            />
        );
        const { rerender } = render(<Advanced loading={false} />);
        const date = screen.getByPlaceholderText("Select date");
        date.focus();
        rerender(<Advanced loading />);
        expect(date).toHaveFocus();
        expect(date).not.toBeDisabled();
        fireEvent.change(date, { target: { value: "2026/01/02" } });
        expect(onFilterChange).not.toHaveBeenCalled();
    });

    it("keeps the metadata panel's Add filter focusable with the reason, and ignores a press", async () => {
        const user = userEvent.setup({ delay: null });
        const onAddFilter = jest.fn();
        const Metadata: React.FC<{ disabled: boolean }> = ({ disabled }) => (
            <MetadataSearchPanel
                metadataFilters={[{ key: "product", value: "pump", operator: "=", type: "string" }]}
                onAddFilter={onAddFilter}
                onRemoveFilter={jest.fn()}
                onUpdateFilter={jest.fn()}
                disabled={disabled}
            />
        );
        const { rerender } = render(<Metadata disabled={false} />);
        const add = screen.getByRole("button", { name: "Add Metadata Filter" });
        add.focus();
        rerender(<Metadata disabled />);
        expect(add).toHaveFocus();
        expect(add).not.toBeDisabled();
        expect(add).toHaveAttribute("aria-disabled", "true");
        expect(describedBy(add)).toContain(SEARCH_RUNNING);
        await user.click(add);
        expect(onAddFilter).not.toHaveBeenCalled();
        const value = screen.getByPlaceholderText("Enter value (use * for wildcard)");
        expect(value).not.toBeDisabled();
        expect(value).toHaveAttribute("readonly");
    });

    it("keeps the location filter's Apply focusable with the reason, its fields read-only", async () => {
        const user = userEvent.setup({ delay: null });
        const onFilterChange = jest.fn();
        const Geo: React.FC<{ disabled: boolean }> = ({ disabled }) => (
            <GeoFilterPanel
                filters={{} as any}
                onFilterChange={onFilterChange}
                disabled={disabled}
            />
        );
        const { rerender } = render(<Geo disabled={false} />);
        const apply = screen.getByRole("button", { name: "Apply" });
        apply.focus();
        rerender(<Geo disabled />);
        expect(apply).toHaveFocus();
        expect(apply).toHaveAttribute("aria-disabled", "true");
        expect(apply).not.toBeDisabled();
        await user.click(apply);
        expect(onFilterChange).not.toHaveBeenCalled();
        expect(screen.getByPlaceholderText("47.6062")).toHaveAttribute("readonly");
        // Nothing to clear: unavailable for another reason, so it stays disabled.
        expect(screen.getByRole("button", { name: "Clear" })).toBeDisabled();
    });

    it("keeps the preferences' Reset focusable with the reason", () => {
        const Preferences: React.FC<{ disabled: boolean }> = ({ disabled }) => (
            <PreferencesPanel
                preferences={{ pageSize: 50, assetTableColumns: [], fileTableColumns: [] } as any}
                onPreferencesChange={jest.fn()}
                recordType="asset"
                disabled={disabled}
                filters={{} as any}
                onFilterChange={jest.fn()}
            />
        );
        const { rerender } = render(<Preferences disabled={false} />);
        const reset = screen.getByRole("button", { name: "Reset to Defaults" });
        reset.focus();
        rerender(<Preferences disabled />);
        expect(reset).toHaveFocus();
        expect(reset).toHaveAttribute("aria-disabled", "true");
        expect(reset).not.toBeDisabled();
    });
});
