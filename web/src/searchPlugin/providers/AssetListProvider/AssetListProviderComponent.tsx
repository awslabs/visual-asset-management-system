/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Button from "@cloudscape-design/components/button";
import Flashbar from "@cloudscape-design/components/flashbar";
import Toggle from "@cloudscape-design/components/toggle";
import ListPage from "../../../pages/ListPage";
import { AssetListDefinition } from "../../../components/list/list-definitions/AssetListDefinition";
import AssetDeleteModal from "../../../components/modals/AssetDeleteModal";
import AssetUnarchiveModal from "../../../components/modals/AssetUnarchiveModal";
import { fetchAllAssets, fetchDatabaseAssets } from "../../../services/APIService";
import Synonyms from "../../../synonyms";
import type { SearchProviderProps } from "../../core/types";

/** An archived row: the API lists it from the `#deleted` partition and marks it `status: archived`. */
export function isArchivedAsset(item: any): boolean {
    return item?.status === "archived" || String(item?.databaseId || "").endsWith("#deleted");
}

/** Rows as the table shows them: the base database id (the partition suffix is an API detail). */
export function normalizeListedAsset(item: any): any {
    const archived = isArchivedAsset(item);
    return {
        ...item,
        databaseId: String(item?.databaseId || "").replace(/#deleted$/, ""),
        ...(archived ? { status: "archived" } : {}),
    };
}

/**
 * The asset list: every accessible asset from the asset API, paged and filtered client-side. Needs
 * no search engine, so it is offered on every deployment. ListPage reads the database from the route.
 * Bulk archive / permanent delete and unarchive use the same modals as the Primary Search tab, so the
 * two surfaces behave identically and the actions stay reachable when no engine is enabled.
 */
const AssetListProviderComponent: React.FC<SearchProviderProps> = ({ databaseId }) => {
    const navigate = useNavigate();
    const [showArchived, setShowArchived] = useState(false);
    const [selectedItems, setSelectedItems] = useState<any[]>([]);
    const [showDeleteModal, setShowDeleteModal] = useState(false);
    const [showUnarchiveModal, setShowUnarchiveModal] = useState(false);
    const [notice, setNotice] = useState<any[]>([]);
    const reloadRef = useRef<(() => void) | null>(null);

    const handleCreateAsset = () => {
        if (databaseId) {
            navigate(`/upload/${databaseId}`);
        } else {
            navigate("/upload");
        }
    };

    // Memoized so ListPage's fetch-option effect fires on a real change only.
    const fetchOptions = useMemo(() => ({ showArchived }), [showArchived]);

    const fetchAll = useCallback(async (options: any) => {
        const items = await fetchAllAssets(options);
        return Array.isArray(items) ? items.map(normalizeListedAsset) : items;
    }, []);
    const fetchForDatabase = useCallback(async (options: any) => {
        const items = await fetchDatabaseAssets(options);
        return Array.isArray(items) ? items.map(normalizeListedAsset) : items;
    }, []);

    const handleReloadReady = useCallback((reload: () => void) => {
        reloadRef.current = reload;
    }, []);

    const showUnarchiveButton = selectedItems.length === 1 && isArchivedAsset(selectedItems[0]);
    const hasArchivedSelected = selectedItems.some(isArchivedAsset);

    const announce = (header: string) => {
        setNotice([
            {
                type: "success",
                header,
                content:
                    "Changes may take a few minutes to propagate throughout the system, including search results.",
                dismissible: true,
                dismissLabel: "Dismiss message",
                onDismiss: () => setNotice([]),
            },
        ]);
    };

    const refresh = () => {
        if (reloadRef.current) reloadRef.current();
    };

    // An array, not a Fragment: TableList places these inside a SpaceBetween, which lays out its
    // direct children.
    const headerActions = [
        <Button
            key="delete"
            disabled={selectedItems.length === 0}
            onClick={() => setShowDeleteModal(true)}
        >
            Delete Selected
        </Button>,
        ...(showUnarchiveButton
            ? [
                  <Button key="unarchive" onClick={() => setShowUnarchiveModal(true)}>
                      Unarchive Selected
                  </Button>,
              ]
            : []),
    ];

    const filterControls = (
        <Toggle checked={showArchived} onChange={({ detail }) => setShowArchived(detail.checked)}>
            Show archived
        </Toggle>
    );

    return (
        <>
            {notice.length > 0 && <Flashbar items={notice} />}
            <ListPage
                singularName={Synonyms.Asset}
                singularNameTitleCase={Synonyms.Asset}
                pluralName={Synonyms.assets}
                pluralNameTitleCase={Synonyms.Assets}
                onCreateCallback={handleCreateAsset}
                listDefinition={AssetListDefinition}
                fetchAllElements={fetchAll}
                fetchElements={fetchForDatabase}
                fetchOptions={fetchOptions}
                includeArchived={showArchived}
                hideDeleteButton={true}
                customHeaderActions={headerActions}
                customFilterControls={filterControls}
                onSelectionChange={setSelectedItems}
                onReloadReady={handleReloadReady}
            />

            <AssetDeleteModal
                visible={showDeleteModal}
                onDismiss={() => setShowDeleteModal(false)}
                mode="asset"
                selectedAssets={selectedItems}
                forceDeleteMode={hasArchivedSelected}
                onSuccess={(operation) => {
                    setShowDeleteModal(false);
                    announce(
                        `${Synonyms.Asset} ${
                            operation === "archive" ? "archived" : "permanently deleted"
                        } successfully`
                    );
                    refresh();
                }}
            />

            <AssetUnarchiveModal
                visible={showUnarchiveModal}
                onDismiss={() => setShowUnarchiveModal(false)}
                selectedAsset={selectedItems[0]}
                onSuccess={() => {
                    setShowUnarchiveModal(false);
                    announce(`${Synonyms.Asset} unarchived successfully`);
                    refresh();
                }}
            />
        </>
    );
};

export default AssetListProviderComponent;
