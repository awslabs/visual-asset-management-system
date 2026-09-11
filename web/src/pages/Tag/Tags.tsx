/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import CreateTag from "./CreateTag";
import ListDefinition from "../../components/list/list-definitions/types/ListDefinition";
import ColumnDefinition from "../../components/list/list-definitions/types/ColumnDefinition";
import ListPageNoDatabase from "../ListPageNoDatabase";
import CreateTagType from "./CreateTagType";
import { fetchTags, fetchtagTypes, deleteTag, deleteTagType } from "../../services/APIService";
import React, { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Box, Button, Header, SpaceBetween } from "@cloudscape-design/components";
import DatabaseSelectionRequired from "../../components/selectors/DatabaseSelectionRequired";
import DatabaseSelectorWithModal from "../../components/selectors/DatabaseSelectorWithModal";
import Synonyms from "../../synonyms";
import { scopeDisplayLabel } from "../../common/utils/databaseScope";
import { usePageTitle } from "../../hooks/usePageTitle";
import ScopeBadge from "../../components/common/ScopeBadge";
let rel;

export const TagsListDefinition = new ListDefinition({
    pluralName: "tags",
    pluralNameTitleCase: "Tags",
    singularNameTitleCase: "Tag",
    visibleColumns: ["tagName", "description", "tagTypeName", "databaseId"],
    filterColumns: [{ name: "tagName", placeholder: "Name" }],
    elementId: "tagName",
    deleteFunction: async (item: any): Promise<[boolean, string, string]> => {
        try {
            // The row's own scope must go with the name: a tag is identified by scope AND name.
            const result: any = await deleteTag({
                tagName: item.tagName,
                databaseId: item.databaseId,
            });
            return [result[0], result[1] || "", ""];
        } catch (error: any) {
            console.log(error);
            return [false, error?.message || "Failed to delete tag", error?.message];
        }
    },
    columnDefinitions: [
        new ColumnDefinition({
            id: "tagName",
            header: "Name",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "tagName",
        }),
        new ColumnDefinition({
            id: "description",
            header: "Description",
            cellWrapper: (props: any) => (
                <span style={{ whiteSpace: "normal", wordBreak: "break-word" }}>
                    {props.children}
                </span>
            ),
            sortingField: "description",
        }),
        new ColumnDefinition({
            id: "tagTypeName",
            header: "Tag Type",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "tagTypeName",
        }),
        new ColumnDefinition({
            id: "databaseId",
            header: "Scope",
            cellWrapper: (props: any) => <ScopeBadge databaseId={props.item?.databaseId} />,
            sortingField: "databaseId",
        }),
    ],
});

export const TagTypesListDefinition = new ListDefinition({
    pluralName: "tag types",
    pluralNameTitleCase: "Tag Types",
    singularNameTitleCase: "Tag Type",
    visibleColumns: ["tagTypeName", "description", "required", "tags", "databaseId"],
    filterColumns: [{ name: "name", placeholder: "Name" }],
    elementId: "tagTypeName",
    deleteFunction: async (item: any): Promise<[boolean, string, string]> => {
        try {
            // The row's own scope must go with the name (see the tag delete above).
            const result: any = await deleteTagType({
                tagTypeName: item.tagTypeName,
                databaseId: item.databaseId,
            });
            return [result[0], result[1] || "", ""];
        } catch (error: any) {
            console.log(error);
            return [false, error?.message || "Failed to delete tag type", error?.message];
        }
    },
    columnDefinitions: [
        new ColumnDefinition({
            id: "tagTypeName",
            header: "Tag Type",
            cellWrapper: (props: any) => {
                return <>{props.children}</>;
            },
            sortingField: "tagTypeName",
        }),
        new ColumnDefinition({
            id: "description",
            header: "Description",
            cellWrapper: (props: any) => (
                <span style={{ whiteSpace: "normal", wordBreak: "break-word" }}>
                    {props.children}
                </span>
            ),
            sortingField: "description",
        }),
        new ColumnDefinition({
            id: "required",
            header: "Required on Asset",
            cellWrapper: (props: any) => <>{props.children}</>,
            sortingField: "required",
        }),
        new ColumnDefinition({
            id: "tags",
            header: "Tags",
            cellWrapper: (props: any) => (
                <span style={{ whiteSpace: "normal", wordBreak: "break-word" }}>
                    {props.children}
                </span>
            ),
            sortingField: "tags",
        }),
        new ColumnDefinition({
            id: "databaseId",
            header: "Scope",
            cellWrapper: (props: any) => <ScopeBadge databaseId={props.item?.databaseId} />,
            sortingField: "databaseId",
        }),
    ],
});

const GLOBAL_SCOPE = "GLOBAL";

/**
 * Fetch tags/tag types for exactly the scope the page is showing.
 *
 * A databaseId query returns only that database's entries, and scope=global only the shared ones —
 * which is what tag ADMINISTRATION wants: you manage one scope at a time. (The asset forms are the
 * case that needs both at once, and they merge two calls.)
 */
function fetchForScope(
    fetchFn: (params?: { databaseId?: string; scope?: "global" | "all" }) => any,
    scope: string
) {
    return scope === GLOBAL_SCOPE ? fetchFn({ scope: "global" }) : fetchFn({ databaseId: scope });
}
export default function Tags() {
    usePageTitle("Tag Management");
    const params = useParams();
    const navigate = useNavigate();

    // The scope being administered comes from the route, the same way the metadata-schema page works,
    // so database selection is one mechanism across the app rather than a per-page control.
    const scope = params.databaseId;

    const [reloadKey1, setReloadKey1] = useState(0);
    const [reloadKey2, setReloadKey2] = useState(100);
    const [changeDatabaseOpen, setChangeDatabaseOpen] = useState(false);

    const reloadChild1 = () => setReloadKey2((k) => k - 1);
    const reloadChild2 = () => setReloadKey1((k) => k + 1);

    const scopedFetchTags = () => fetchForScope(fetchTags, scope || GLOBAL_SCOPE);
    const scopedFetchTagTypes = () => fetchForScope(fetchtagTypes, scope || GLOBAL_SCOPE);

    // The create/edit forms show the scope as a read-only field taken from the page, so a tag can only
    // ever be created in the scope on screen. Memoized on the scope: a fresh component identity on
    // every render would remount the form and discard what the user had typed.
    const CreateTagInScope = useMemo(() => {
        const WithScope = (props: any) => <CreateTag {...props} lockedDatabaseId={scope} />;
        WithScope.displayName = "CreateTagInScope";
        return WithScope;
    }, [scope]);
    const CreateTagTypeInScope = useMemo(() => {
        const WithScope = (props: any) => <CreateTagType {...props} lockedDatabaseId={scope} />;
        WithScope.displayName = "CreateTagTypeInScope";
        return WithScope;
    }, [scope]);

    if (!scope) {
        return (
            <DatabaseSelectionRequired
                title="Tag Management"
                description={`Tags and tag types are managed per ${Synonyms.database}.`}
                onSelect={(event: any) => {
                    const id = event?.detail?.selectedOption?.value;
                    if (id) {
                        navigate(`/auth/tags/${id}`);
                    }
                }}
            />
        );
    }

    return (
        <>
            <Box padding={{ top: "m", horizontal: "l" }}>
                <Header
                    variant="h1"
                    description={`${Synonyms.Database}: ${scopeDisplayLabel(scope)}`}
                    actions={
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button onClick={() => setChangeDatabaseOpen(true)}>
                                {`Change ${Synonyms.Database}`}
                            </Button>
                        </SpaceBetween>
                    }
                >
                    Tag Management
                </Header>
            </Box>
            <ListPageNoDatabase
                singularName={"tag"}
                singularNameTitleCase={"Tag"}
                pluralName={"tags"}
                pluralNameTitleCase={"Manage Tags"}
                // The key carries the scope: the list only fetches on mount, so without this a scope
                // change left the previous scope's rows on screen.
                listDefinition={TagsListDefinition}
                CreateNewElement={CreateTagInScope}
                fetchElements={scopedFetchTags}
                fetchAllElements={scopedFetchTags}
                editEnabled={true}
                key={`${scope}-${reloadKey1}`}
                onReload={reloadChild1}
            />
            <ListPageNoDatabase
                singularName={"tag type"}
                singularNameTitleCase={"Tag Type"}
                pluralName={"tag type"}
                pluralNameTitleCase={""}
                listDefinition={TagTypesListDefinition}
                CreateNewElement={CreateTagTypeInScope}
                fetchElements={scopedFetchTagTypes}
                fetchAllElements={scopedFetchTagTypes}
                editEnabled={true}
                key={`${scope}-${reloadKey2}`}
                onReload={reloadChild2}
            />
            {/* Changing scope is a modal here: dismissing it returns to a populated page. */}
            <DatabaseSelectorWithModal
                open={changeDatabaseOpen}
                setOpen={setChangeDatabaseOpen}
                showGlobal={true}
                onSelectorChange={(event: any) => {
                    const id = event?.detail?.selectedOption?.value;
                    if (id) {
                        setChangeDatabaseOpen(false);
                        navigate(`/auth/tags/${id}`);
                    }
                }}
            />
        </>
    );
}
