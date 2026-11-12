/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Button from "@cloudscape-design/components/button";

export interface ListToolbarProps {
    /** The search field, on the left. */
    search?: React.ReactNode;
    /** Reloads the list; the refresh button sits next to the search (at the left without one). */
    onRefresh?: () => void;
    /** Shows the refresh button busy while the list loads. */
    refreshing?: boolean;
    /** The refresh button's accessible name, naming what it reloads. */
    refreshLabel?: string;
    /** The filter drop-downs, aligned to the right of the table. */
    filters?: React.ReactNode;
}

const ROW_STYLE: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    flexWrap: "wrap",
};

/**
 * The toolbar of a list page, passed as a Cloudscape Table's `filter`: the search field on the
 * left with the refresh button right next to it, and the filter drop-downs aligned to the right.
 * On a narrow screen the groups wrap. The layout is `TableList`'s filter slot
 * (`components/list/TableList.tsx`); the search group grows, which pushes the filters to the right.
 */
const ListToolbar: React.FC<ListToolbarProps> = ({
    search,
    onRefresh,
    refreshing = false,
    refreshLabel = "Refresh data",
    filters,
}) => (
    <div data-list-toolbar="" style={ROW_STYLE}>
        <div
            data-list-toolbar-group="search"
            style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                flex: "1 1 250px",
                minWidth: "200px",
            }}
        >
            {search}
            {onRefresh && (
                <Button
                    iconName="refresh"
                    variant="icon"
                    onClick={onRefresh}
                    loading={refreshing}
                    ariaLabel={refreshLabel}
                />
            )}
        </div>
        {filters && (
            <div data-list-toolbar-group="filters" style={ROW_STYLE}>
                {filters}
            </div>
        )}
    </div>
);

export default ListToolbar;
