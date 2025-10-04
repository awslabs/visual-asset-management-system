import Table, { TableProps } from "@cloudscape-design/components/table";
import {
    CollectionPreferences,
    Header,
    Link,
    Pagination,
    Box,
    Button,
    SpaceBetween,
    Alert,
    Input,
    Grid,
    Select,
    FormField,
    Modal,
    Popover,
    Icon,
} from "@cloudscape-design/components";
import { SearchExplanation } from "../../components/search/types";
import AssetDeleteModal from "../../components/modals/AssetDeleteModal";
import PreviewThumbnailCell from "../../components/search/SearchPreviewThumbnail/PreviewThumbnailCell";
import FilePreviewThumbnailCell from "../../components/search/SearchPreviewThumbnail/FilePreviewThumbnailCell";
import AssetPreviewModal from "../../components/filemanager/modals/AssetPreviewModal";
import {
    changeFilter,
    changeRectype,
    paginateSearch,
    search,
    sortSearch,
} from "./SearchPropertyFilter";
import { INITIAL_STATE, SearchPageViewProps } from "./SearchPageTypes";
import Synonyms from "../../synonyms";
import { EmptyState } from "../../common/common-components";
import { useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { fetchtagTypes } from "../../services/APIService";
import { formatFileSizeForDisplay } from "../../common/utils/fileSize";
import { Checkbox } from "@cloudscape-design/components";

var tagTypes: any;
//let databases: any;

// Helper component to render explanation popover
const ExplanationPopover: React.FC<{ explanation: SearchExplanation }> = ({ explanation }) => (
    <Popover
        size="large"
        position="right"
        triggerType="custom"
        dismissButton={false}
        content={
            <SpaceBetween size="s">
                <Box variant="h4">Why this result matched</Box>
                <Box>
                    <strong>Query Type:</strong> {explanation.query_type}
                </Box>
                <Box>
                    <strong>Index:</strong> {explanation.index_type}
                </Box>
                <Box>
                    <strong>Score:</strong> {explanation.score_breakdown.total_score.toFixed(2)}
                </Box>
                {explanation.matched_fields.length > 0 && (
                    <>
                        <Box variant="h5">Matched Fields ({explanation.matched_fields.length}):</Box>
                        <Box>
                            <ul style={{ margin: 0, paddingLeft: '20px' }}>
                                {explanation.matched_fields.slice(0, 5).map((field, idx) => (
                                    <li key={idx}>
                                        <strong>{field}:</strong> {explanation.match_reasons[field] || 'Matched'}
                                    </li>
                                ))}
                                {explanation.matched_fields.length > 5 && (
                                    <li>...and {explanation.matched_fields.length - 5} more fields</li>
                                )}
                            </ul>
                        </Box>
                    </>
                )}
            </SpaceBetween>
        }
    >
        <Icon name="status-info" variant="link" />
    </Popover>
);

// Helper component to render metadata popover
const MetadataPopover: React.FC<{ metadata: Array<{name: string, type: string, value: any}> }> = ({ metadata }) => {
    if (metadata.length === 0) {
        return <span></span>;
    }
    
    return (
        <Popover
            size="large"
            position="right"
            triggerType="custom"
            dismissButton={false}
            content={
                <SpaceBetween size="s">
                    <Box variant="h4">Metadata Fields ({metadata.length})</Box>
                    <Box>
                        <ul style={{ margin: 0, paddingLeft: '20px' }}>
                            {metadata.map((field, idx) => (
                                <li key={idx}>
                                    <strong>{field.name} ({field.type}):</strong> {String(field.value)}
                                </li>
                            ))}
                        </ul>
                    </Box>
                </SpaceBetween>
            }
        >
            <Icon name="status-info" variant="link" />
        </Popover>
    );
};

// Helper function to extract and format metadata fields with type information
const extractMetadata = (item: any): Array<{name: string, type: string, value: any}> => {
    const metadata: Array<{name: string, type: string, value: any}> = [];
    
    // Type mapping for display
    const typeLabels: Record<string, string> = {
        'str': 'String',
        'num': 'Number',
        'bool': 'Boolean',
        'date': 'Date',
        'list': 'List',
        'gp': 'Geo Point',
        'gs': 'Geo Shape',
    };
    
    // Find all MD_ fields
    Object.keys(item).forEach(key => {
        if (key.startsWith('MD_')) {
            // Format: MD_<type>_<fieldname>
            const parts = key.split('_');
            if (parts.length >= 3) {
                // parts[0] = 'MD', parts[1] = type, parts[2+] = field name
                const fieldType = parts[1];
                const fieldName = parts.slice(2).join('_');
                metadata.push({
                    name: fieldName,
                    type: typeLabels[fieldType] || fieldType,
                    value: item[key]
                });
            } else {
                // Fallback: just remove MD_ prefix
                metadata.push({
                    name: key.substring(3),
                    type: 'Unknown',
                    value: item[key]
                });
            }
        }
    });
    
    return metadata;
};

function columnRender(e: any, name: string, value: any, navigate?: any, isFileMode?: boolean) {
    if (name === "str_databaseid") {
        return (
            <Box>
                <Link href={`#/databases/${e["str_databaseid"]}/assets/`}>{value}</Link>
            </Box>
        );
    }
    if (name === "str_assetname") {
        return (
            <Box>
                <SpaceBetween direction="horizontal" size="xs">
                    <Link href={`#/databases/${e["str_databaseid"]}/assets/${e["str_assetid"]}`}>
                        {value}
                    </Link>
                    {/* Only show explanation in asset mode, not file mode */}
                    {e.explanation && !isFileMode && <ExplanationPopover explanation={e.explanation} />}
                </SpaceBetween>
            </Box>
        );
    } else if (name === "str_key") {
        // File path - clickable in file mode to navigate to asset with file selected
        if (isFileMode && navigate) {
            return (
                <Box>
                    <SpaceBetween direction="horizontal" size="xs">
                        <Link 
                            href={`#/databases/${e["str_databaseid"]}/assets/${e["str_assetid"]}`}
                            onFollow={(event) => {
                                event.preventDefault();
                                navigate(`/databases/${e["str_databaseid"]}/assets/${e["str_assetid"]}`, {
                                    state: { filePathToNavigate: value }
                                });
                            }}
                        >
                            {value}
                        </Link>
                        {/* Show explanation icon on file path in file mode */}
                        {e.explanation && <ExplanationPopover explanation={e.explanation} />}
                    </SpaceBetween>
                </Box>
            );
        } else {
            // Non-file mode - just show as text without explanation (explanation shown on asset name)
            return (
                <Box>
                    <SpaceBetween direction="horizontal" size="xs">
                        <span>{value}</span>
                    </SpaceBetween>
                </Box>
            );
        }
    } else if (name === "list_tags" && Array.isArray(value)) {
        const tagsWithType = value.map((tag) => {
            if (tagTypes)
                for (const tagType of tagTypes) {
                    var tagTypeName = tagType.tagTypeName;

                    //If tagType has required field add [R] to tag type name
                    if (tagType && tagType.required === "True") {
                        tagTypeName += " [R]";
                    }

                    if (tagType.tags.includes(tag)) {
                        return `${tag} (${tagTypeName})`;
                    }
                }
            return tag;
        });

        return <Box>{tagsWithType.join(", ")}</Box>;
    } else if (name.startsWith("bool_")) {
        // Display all boolean fields as checkboxes
        return (
            <Box>
                <Checkbox checked={value === true} disabled />
            </Box>
        );
    } else if (name === "num_filesize" || name === "num_size") {
        // Format file size to human-readable format
        return <Box>{formatFileSizeForDisplay(value)}</Box>;
    } else if (name.indexOf("date_") === 0) {
        // Format dates to human-readable format
        try {
            const date = new Date(value);
            return <Box>{date.toLocaleString()}</Box>;
        } catch {
            return <Box>{value}</Box>;
        }
    } else if (
        name.indexOf("str") === 0 ||
        name.indexOf("num_") === 0
    ) {
        return <Box>{value}</Box>;
    }
}

function SearchPageListView({ state, dispatch }: SearchPageViewProps) {
    // identify all the names of columns from state.result.hits.hits
    // create a column definition for each column
    const [showDeleteModal, setShowDeleteModal] = useState(false);
    const [showPreviewModal, setShowPreviewModal] = useState(false);
    const [previewAsset, setPreviewAsset] = useState<{
        url?: string;
        assetId?: string;
        databaseId?: string;
        previewKey?: string;
        assetName?: string;
        downloadType?: "assetPreview" | "assetFile";
    }>({});

    useEffect(() => {
        fetchtagTypes().then((res) => {
            tagTypes = res;
        });
    }, []);

    const navigate = useNavigate();

    // Handler for opening the preview modal
    const handleOpenPreview = (
        previewUrl: string,
        assetName: string,
        previewKey: string,
        downloadTypeOrItem?: "assetPreview" | "assetFile" | any,
        itemData?: any
    ) => {
        // Handle both old signature (item as 4th param) and new signature (downloadType as 4th param)
        let downloadType: "assetPreview" | "assetFile" = "assetPreview";
        let item: any = undefined;
        
        if (typeof downloadTypeOrItem === 'string' && 
            (downloadTypeOrItem === 'assetPreview' || downloadTypeOrItem === 'assetFile')) {
            // New signature: downloadType passed, item might be in 5th param
            downloadType = downloadTypeOrItem;
            item = itemData;
        } else if (downloadTypeOrItem && typeof downloadTypeOrItem === 'object') {
            // Old signature: item passed as 4th param
            item = downloadTypeOrItem;
        }
        
        setPreviewAsset({
            url: previewUrl,
            assetId: item?.str_assetid,
            databaseId: item?.str_databaseid,
            previewKey: previewKey,
            assetName: assetName,
            downloadType: downloadType,
        });
        setShowPreviewModal(true);
    };

    if (!state?.initialResult) {
        return <div>Loading..</div>;
    }

    const { columnNames } = state;

    // Use tablePreferences.visibleContent for column order if available, otherwise use columnNames
    const orderedColumnNames = state.tablePreferences?.visibleContent || columnNames;

    // Determine if we're in file mode
    const isFileMode = state.filters._rectype.value === "file";

    // Filter out undefined/null column names and add preview column if showPreviewThumbnails is enabled
    let enhancedColumnDefinitions = orderedColumnNames?.filter((name: string) => name)?.map((name: string) => {
        // Custom headers based on record type

        if (name === "str_asset") {
            return {
                id: name,
                header: Synonyms.Asset,
                cell: (e: any) => columnRender(e, name, e[name], navigate, isFileMode),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "str_databaseid") {
            return {
                id: name,
                header: Synonyms.Database,
                cell: (e: any) => columnRender(e, name, e[name], navigate, isFileMode),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "str_assettype") {
            return {
                id: name,
                header: isFileMode ? "Asset Type" : "Type",
                cell: (e: any) => columnRender(e, name, e[name], navigate, isFileMode),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "list_tags") {
            return {
                id: name,
                header: isFileMode ? "Asset Tags" : "Tags",
                cell: (e: any) => columnRender(e, name, e[name], navigate, isFileMode),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "str_key" && isFileMode) {
            return {
                id: name,
                header: "File Path",
                cell: (e: any) => columnRender(e, name, e[name], navigate, isFileMode),
                sortingField: name,
                isRowHeader: false,
            };
        }
        if (name === "str_description" && isFileMode) {
            return {
                id: name,
                header: "Asset Description",
                cell: (e: any) => columnRender(e, name, e[name], navigate, isFileMode),
                sortingField: name,
                isRowHeader: false,
            };
        }
        return {
            id: name,
            header:
                name === "str_assetname"
                    ? Synonyms.Asset
                    : name === "metadata"
                    ? "Metadata"
                    : name
                          .split("_")
                          .slice(1)
                          .map((s: string) => s.charAt(0).toUpperCase() + s.slice(1))
                          .join(" "),
            cell: (e: any) => name === "metadata" ? <MetadataPopover metadata={extractMetadata(e)} /> : columnRender(e, name, e[name], navigate, isFileMode),
            sortingField: name === "metadata" ? undefined : name,
            isRowHeader: false,
        };
    });

    // No need to rearrange columns - they should already be in the correct order from preferences

    // Add or remove preview column based on showPreviewThumbnails toggle
    if (state.showPreviewThumbnails) {
        // Remove any existing preview columns first to avoid duplicates
        enhancedColumnDefinitions = enhancedColumnDefinitions.filter((col: any) => col.id !== "preview");
        
        // Different preview cell based on record type
        if (state.filters._rectype.value === "asset") {
            // Asset preview cell
            enhancedColumnDefinitions = [
                {
                    id: "preview",
                    header: "Preview",
                    cell: (item: any) => (
                        <PreviewThumbnailCell
                            assetId={item.str_assetid}
                            databaseId={item.str_databaseid}
                            onOpenFullPreview={(url, assetName, previewKey) =>
                                handleOpenPreview(url, assetName, previewKey, item)
                            }
                            assetName={item.str_assetname}
                        />
                    ),
                    sortingField: "preview",
                    isRowHeader: false,
                },
                ...enhancedColumnDefinitions,
            ];
        } else if (state.filters._rectype.value === "file") {
            // File preview cell
            enhancedColumnDefinitions = [
                {
                    id: "preview",
                    header: "Preview",
                    cell: (item: any) => (
                        <FilePreviewThumbnailCell
                            assetId={item.str_assetid}
                            databaseId={item.str_databaseid}
                            fileKey={item.str_key}
                            fileName={item.str_key?.split("/").pop() || item.str_key || ""}
                            fileSize={item.num_filesize || item.num_size}
                            onOpenFullPreview={(url, fileName, previewKey, downloadType) =>
                                handleOpenPreview(url, fileName, previewKey, downloadType, item)
                            }
                        />
                    ),
                    sortingField: "preview",
                    isRowHeader: false,
                },
                ...enhancedColumnDefinitions,
            ];
        }

        // Add preview to visible columns if not already there
        if (
            state.tablePreferences?.visibleContent &&
            !state.tablePreferences.visibleContent.includes("preview")
        ) {
            state.tablePreferences.visibleContent = [
                "preview",
                ...state.tablePreferences.visibleContent,
            ];
        }
    } else {
        // Remove preview column when toggle is off
        enhancedColumnDefinitions = enhancedColumnDefinitions.filter((col: any) => col.id !== "preview");
        
        // Remove preview from visible columns
        if (state.tablePreferences?.visibleContent) {
            state.tablePreferences.visibleContent = state.tablePreferences.visibleContent.filter(
                (col: string) => col !== "preview"
            );
        }
    }

    const currentPage = 1 + Math.floor(state?.pagination?.from / state?.tablePreferences?.pageSize);
    const pageCount = Math.ceil(
        state?.result?.hits?.total?.value / state?.tablePreferences?.pageSize
    );
    
    console.log('[SearchPageListView] Pagination calculation:', {
        from: state?.pagination?.from,
        pageSize: state?.tablePreferences?.pageSize,
        currentPage,
        pageCount,
        totalResults: state?.result?.hits?.total?.value
    });

    if (!enhancedColumnDefinitions) {
        return <div>Loading...</div>;
    }

    // Debug logging
    console.log('SearchPageListView render:', {
        tableSort: state.tableSort,
        sortingField: state?.tableSort?.sortingField,
        sortingDescending: state?.tableSort?.sortingDescending,
        selectedItems: state?.selectedItems,
        selectedCount: state?.selectedItems?.length
    });

    return (
        <>
            <SpaceBetween direction="vertical" size="l">
                <Table
                    empty={
                        <EmptyState
                            title="No matches"
                            subtitle="We can't find a match."
                            action={
                                <Button
                                    onClick={() => {
                                        dispatch({ type: "query-criteria-cleared" });
                                        setTimeout(() => {
                                            search(INITIAL_STATE, { state, dispatch });
                                        }, 10);
                                    }}
                                >
                                    Clear filter
                                </Button>
                            }
                        />
                    }
                    columnDefinitions={enhancedColumnDefinitions}
                    selectedItems={state?.selectedItems}
                    isItemDisabled={(item: any) => {
                        return state?.disableSelection || false;
                    }}
                    onSelectionChange={({ detail }) => {
                        if (detail.selectedItems) {
                            dispatch({
                                type: "set-selected-items",
                                selectedItems: detail.selectedItems,
                            });
                        }
                    }}
                    selectionType={state?.filters._rectype.value === "asset" ? "multi" : undefined}
                    trackBy="_id"
                    visibleColumns={state?.tablePreferences?.visibleContent}
                    loading={state.loading}
                    loadingText="Loading"
                    items={state?.result?.hits?.hits?.map((hit: any) => ({
                        ...hit._source,
                        _id: hit._id,
                        explanation: hit.explanation,
                    }))}
                    sortingColumn={state?.tableSort?.sortingField ? {
                        sortingField: state?.tableSort?.sortingField,
                    } : undefined}
                    sortingDescending={!!state?.tableSort?.sortingDescending}
                    onSortingChange={({ detail }) => {
                        console.log("sorting change", detail);
                        const sortingField = detail.sortingColumn?.sortingField;
                        if (sortingField) {
                            // Build sort query for backend
                            let sortingFieldIndex = sortingField;
                            if (sortingField.indexOf("str_") === 0) {
                                sortingFieldIndex = sortingField + ".keyword";
                            }
                            
                            const sort = [
                                {
                                    field: sortingFieldIndex,
                                    order: detail.isDescending ? "desc" : "asc",
                                },
                                "_score",
                            ];
                            
                            const tableSort = {
                                sortingField,
                                sortingDescending: detail.isDescending ?? false,
                            };
                            
                            // Dispatch action - let the parent component handle the actual search
                            dispatch({
                                type: "query-sort",
                                sort,
                                tableSort,
                            });
                        }
                    }}
                    pagination={
                        <Pagination
                            pagesCount={pageCount}
                            currentPageIndex={currentPage}
                            onChange={({ detail }) => {
                                console.log(
                                    "pagination change",
                                    detail,
                                    state?.tablePreferences?.pageSize
                                );
                                // Just dispatch the action - let ModernSearchContainer handle the search
                                dispatch({
                                    type: "query-paginate",
                                    pagination: {
                                        from: (detail.currentPageIndex - 1) * state?.tablePreferences?.pageSize,
                                        size: state?.tablePreferences?.pageSize,
                                    },
                                });
                            }}
                        />
                    }
                    preferences={
                        null as any /* Hidden - preferences managed in sidebar */
                    }
                    /* Commented out preferences gear icon - managed in sidebar instead
                    preferences={
                        <CollectionPreferences
                            onConfirm={({ detail }) => {
                                console.log("detail", detail);
                                dispatch({ type: "set-search-table-preferences", payload: detail });
                                if (typeof detail.pageSize === "number") {
                                    paginateSearch(0, detail.pageSize, { state, dispatch });
                                } else {
                                    console.error("Page size is undefined in preferences detail.");
                                }
                            }}
                            visibleContentPreference={{
                                title: "Columns",
                                options: [
                                    {
                                        label: "All columns",
                                        options: enhancedColumnDefinitions
                                            .map(
                                                (
                                                    columnDefinition: TableProps.ColumnDefinition<string>
                                                ) => ({
                                                    id: columnDefinition.id,
                                                    label: columnDefinition.header,
                                                })
                                            )
                                            .map((x: any) => {
                                                if (
                                                    ["str_assetname", "str_key"].indexOf(x.id) >= 0
                                                ) {
                                                    x.alwaysVisible = true;
                                                    x.editable = false;
                                                }
                                                return x;
                                            })
                                            .sort((a: any, b: any) =>
                                                a.label.localeCompare(b.label)
                                            ),
                                    },
                                ],
                            }}
                            title="Preferences"
                            confirmLabel="Confirm"
                            cancelLabel="Cancel"
                            preferences={state.tablePreferences}
                            pageSizePreference={{
                                title: "Page size",
                                options: [
                                    { value: 10, label: "10 resources" },
                                    { value: 25, label: "25 resources" },
                                    { value: 50, label: "50 resources" },
                                    { value: 100, label: "100 resources" },
                                ],
                            }}
                        />
                    }
                    */
                    header={
                        <Header
                            children={state.filters._rectype.value === "file" ? "Files" : Synonyms.Assets}
                            counter={
                                state?.result?.hits?.total?.value
                                    ? state?.result?.hits?.total?.value +
                                      (state?.result?.hits?.total?.relation === "gte" ? "+" : "")
                                    : ""
                            }
                            actions={
                                state.filters._rectype.value === "asset" ? (
                                    <SpaceBetween direction="horizontal" size="xs">
                                        <Button
                                            disabled={
                                                state?.selectedItems?.length === 0 ||
                                                state?.disableSelection
                                            }
                                            onClick={() => {
                                                setShowDeleteModal(true);
                                            }}
                                        >
                                            Delete Selected
                                        </Button>
                                        <Button
                                            onClick={(e) => {
                                                navigate("/upload");
                                            }}
                                            variant="primary"
                                        >
                                            Create {Synonyms.Asset}
                                        </Button>
                                    </SpaceBetween>
                                ) : null
                            }
                        />
                    }
                    filter={
                        false && ( //Disable these for now
                            <Grid
                                gridDefinition={[
                                    { colspan: { default: 7 } },
                                    { colspan: { default: 5 } },
                                ]}
                            >
                                <FormField label="Keywords">
                                    <Grid
                                        gridDefinition={[
                                            { colspan: { default: 9 } },
                                            { colspan: { default: 3 } },
                                        ]}
                                    >
                                        <Input
                                            placeholder="Search"
                                            type="search"
                                            onChange={(e) => {
                                                dispatch({
                                                    type: "query-updated",
                                                    query: e.detail.value,
                                                });
                                            }}
                                            onKeyDown={({ detail }) => {
                                                if (detail.key === "Enter") {
                                                    search({}, { state, dispatch });
                                                }
                                            }}
                                            value={state?.query}
                                        />
                                        <Button
                                            variant="primary"
                                            onClick={(e) => {
                                                search({}, { state, dispatch });
                                            }}
                                        >
                                            Search
                                        </Button>
                                    </Grid>
                                </FormField>
                                <SpaceBetween direction="horizontal" size="xs">
                                    <FormField label="Asset Type">
                                        <Select
                                            selectedOption={
                                                state?.filters?._rectype || {
                                                    label: Synonyms.Assets,
                                                    value: "asset",
                                                }
                                            }
                                            onChange={({ detail }) =>
                                                // changeRectype(e.detail.selectedOption, { state, dispatch })
                                                changeFilter("_rectype", detail.selectedOption, {
                                                    state,
                                                    dispatch,
                                                })
                                            }
                                            options={[
                                                { label: Synonyms.Assets, value: "asset" },
                                                { label: "Files", value: "file" },
                                            ]}
                                            placeholder="Asset Type"
                                        />
                                    </FormField>
                                    <FormField label="File Type">
                                        <Select
                                            selectedOption={state?.filters?.str_assettype}
                                            placeholder="File Type"
                                            options={[
                                                { label: "All", value: "all" },
                                                ...(state?.result?.aggregations?.str_assettype?.buckets.map(
                                                    (b: any) => {
                                                        return {
                                                            label: `${b.key} (${b.doc_count})`,
                                                            value: b.key,
                                                        };
                                                    }
                                                ) || []),
                                            ]}
                                            onChange={({ detail }) =>
                                                changeFilter(
                                                    "str_assettype",
                                                    detail.selectedOption,
                                                    {
                                                        state,
                                                        dispatch,
                                                    }
                                                )
                                            }
                                        />
                                    </FormField>
                                    <FormField label="Database">
                                        <Select
                                            selectedOption={state?.filters?.str_databaseid}
                                            placeholder="Database"
                                            options={[
                                                { label: "All", value: "all" },
                                                //List every database from "databases" variable and then map to result aggregation to display (doc_count) next to each
                                                //We do this because opensearch has a max items it will return in a query which may not be everything across aggregated databases
                                                //Without this, you wouldn't be able to search on other databases not listed due to trimmed results.
                                                // ...(databases?.map((b: any) => {
                                                //     var count = 0
                                                //     //Map through result aggregation to find doc_count for each database
                                                //     state?.result?.aggregations?.str_databaseid?.buckets.map(
                                                //         (c: any) => {
                                                //             if (c.key === b.databaseId) {
                                                //                 count = c.doc_count
                                                //             }
                                                //         }
                                                //     )

                                                //     return {
                                                //         label: `${b.databaseId} (Results: ${count} / Total: ${b.assetCount})`,
                                                //         value: b.databaseId,
                                                //     };

                                                // }) || []),
                                            ]}
                                            onChange={({ detail }) =>
                                                changeFilter(
                                                    "str_databaseid",
                                                    detail.selectedOption,
                                                    {
                                                        state,
                                                        dispatch,
                                                    }
                                                )
                                            }
                                        />
                                    </FormField>
                                </SpaceBetween>
                            </Grid>
                        )
                    }
                />
            </SpaceBetween>
            <AssetDeleteModal
                visible={showDeleteModal}
                onDismiss={() => setShowDeleteModal(false)}
                mode="asset"
                selectedAssets={state?.selectedItems || []}
                onSuccess={(operation) => {
                    setShowDeleteModal(false);
                    // Refresh the search results
                    search({}, { state, dispatch });
                }}
            />

            {/* Asset Preview Modal */}
            <AssetPreviewModal
                visible={showPreviewModal}
                onDismiss={() => setShowPreviewModal(false)}
                assetId={previewAsset.assetId || ""}
                databaseId={previewAsset.databaseId || ""}
                previewKey={previewAsset.previewKey}
                assetName={previewAsset.assetName || ""}
                downloadType={previewAsset.downloadType}
            />
        </>
    );
}

export default SearchPageListView;
