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
    Icon,
} from "@cloudscape-design/components";
import Popover from "@cloudscape-design/components/popover";
import { SearchExplanation, getTotalResultCount, FIELD_MAPPINGS } from "./types";
import AssetDeleteModal from "../modals/AssetDeleteModal";
import AssetUnarchiveModal from "../modals/AssetUnarchiveModal";
import PreviewThumbnailCell from "./SearchPreviewThumbnail/PreviewThumbnailCell";
import FilePreviewThumbnailCell from "./SearchPreviewThumbnail/FilePreviewThumbnailCell";
import AssetPreviewModal from "../filemanager/modals/AssetPreviewModal";
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
import { useEffect, useState, useRef, useCallback } from "react";
import { fetchtagTypes } from "../../services/APIService";
import { formatFileSizeForDisplay } from "../../common/utils/fileSize";
import { Checkbox } from "@cloudscape-design/components";
import MapThumbnail from "./SearchResults/MapThumbnail";
import { appCache } from "../../services/appCache";
import FileViewerModal from "../filemanager/modals/FileViewerModal";
import { FileInfo } from "../../visualizerPlugin/core/types";
import { EYE_ICON_SVG } from "../../visualizerPlugin/components/EyeIconSvg";
import { useViewerRegistryReady } from "../../visualizerPlugin/core/useViewerRegistryReady";
import {
    searchRowToFileInfo,
    isViewableExtension,
    reconcileViewerSelection,
} from "./utils/searchRowToFileInfo";
import { areFilenamesViewableTogether } from "../../visualizerPlugin/core/viewableExtensions";

let tagTypes: any;

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
                        <Box variant="h5">
                            Matched Fields ({explanation.matched_fields.length}):
                        </Box>
                        <Box>
                            <ul style={{ margin: 0, paddingLeft: "20px" }}>
                                {explanation.matched_fields.slice(0, 5).map((field, idx) => (
                                    <li key={idx}>
                                        <strong>{field}:</strong>{" "}
                                        {explanation.match_reasons[field] || "Matched"}
                                    </li>
                                ))}
                                {explanation.matched_fields.length > 5 && (
                                    <li>
                                        ...and {explanation.matched_fields.length - 5} more fields
                                    </li>
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

// Helper component to render metadata and attributes popover
const MetadataPopover: React.FC<{
    metadata: Array<{ name: string; type: string; value: any }>;
    attributes: Array<{ name: string; type: string; value: any }>;
}> = ({ metadata, attributes }) => {
    // Don't show popover if both arrays are empty
    if (metadata.length === 0 && attributes.length === 0) {
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
                    {/* Metadata Fields Section - only show if there are metadata fields */}
                    {metadata.length > 0 && (
                        <>
                            <Box variant="h4">Metadata Fields ({metadata.length})</Box>
                            <Box>
                                <ul style={{ margin: 0, paddingLeft: "20px" }}>
                                    {metadata.map((field, idx) => (
                                        <li key={idx}>
                                            <strong>
                                                {field.name} ({field.type}):
                                            </strong>{" "}
                                            {String(field.value)}
                                        </li>
                                    ))}
                                </ul>
                            </Box>
                        </>
                    )}

                    {/* Attribute Fields Section - only show if there are attribute fields */}
                    {attributes.length > 0 && (
                        <>
                            <Box variant="h4">Attribute Fields ({attributes.length})</Box>
                            <Box>
                                <ul style={{ margin: 0, paddingLeft: "20px" }}>
                                    {attributes.map((field, idx) => (
                                        <li key={idx}>
                                            <strong>
                                                {field.name} ({field.type}):
                                            </strong>{" "}
                                            {String(field.value)}
                                        </li>
                                    ))}
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
};

// Helper function to infer type from value
const inferType = (value: any): string => {
    if (value === null || value === undefined) {
        return "Unknown";
    }
    if (typeof value === "number") {
        return "Number";
    }
    if (typeof value === "boolean") {
        return "Boolean";
    }
    if (Array.isArray(value)) {
        return "List";
    }
    if (typeof value === "string") {
        // Check if it's a date string
        if (!isNaN(Date.parse(value)) && value.match(/^\d{4}-\d{2}-\d{2}/)) {
            return "Date";
        }
        return "String";
    }
    if (typeof value === "object") {
        return "Object";
    }
    return "Unknown";
};

// Helper function to extract and format metadata and attribute fields with type information
const extractMetadata = (
    item: any
): {
    metadata: Array<{ name: string; type: string; value: any }>;
    attributes: Array<{ name: string; type: string; value: any }>;
} => {
    const metadata: Array<{ name: string; type: string; value: any }> = [];
    const attributes: Array<{ name: string; type: string; value: any }> = [];

    // Check if MD_ exists as an object (new format)
    if (item.MD_ && typeof item.MD_ === "object" && !Array.isArray(item.MD_)) {
        Object.entries(item.MD_).forEach(([key, value]) => {
            metadata.push({
                name: key,
                type: inferType(value),
                value: value,
            });
        });
    }

    // Check if AB_ exists as an object (new format)
    if (item.AB_ && typeof item.AB_ === "object" && !Array.isArray(item.AB_)) {
        Object.entries(item.AB_).forEach(([key, value]) => {
            attributes.push({
                name: key,
                type: inferType(value),
                value: value,
            });
        });
    }

    return { metadata, attributes };
};

/**
 * Wraps a Cloudscape Table with a synced horizontal scrollbar above the column headers.
 * Uses a MutationObserver to detect when Cloudscape finishes rendering, then finds
 * its internal scroll container and syncs a visible top scrollbar with it.
 */
const DualScrollWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const topScrollRef = useRef<HTMLDivElement>(null);
    const wrapperRef = useRef<HTMLDivElement>(null);
    const tableScrollElRef = useRef<Element | null>(null);
    const syncing = useRef(false);
    const [scrollInfo, setScrollInfo] = useState({ width: 0, visible: false });

    useEffect(() => {
        const wrapper = wrapperRef.current;
        if (!wrapper) return;

        let cleanupScrollListener: (() => void) | null = null;

        const findAndBind = () => {
            // Find the scrollable container: look for any div with overflow-x auto/scroll
            const allDivs = wrapper.querySelectorAll("div");
            let scrollContainer: Element | null = null;

            for (const div of allDivs) {
                const style = getComputedStyle(div);
                if (style.overflowX === "auto" || style.overflowX === "scroll") {
                    // This is a candidate — check if it has a table inside
                    if (div.querySelector("table")) {
                        scrollContainer = div;
                        break;
                    }
                }
            }

            if (!scrollContainer) return;

            // Detach previous listener if any
            if (cleanupScrollListener) cleanupScrollListener();

            tableScrollElRef.current = scrollContainer;

            // Get the full scrollable width from the table element
            const table = scrollContainer.querySelector("table");
            const fullWidth = table ? table.scrollWidth : scrollContainer.scrollWidth;

            setScrollInfo({ width: fullWidth, visible: true });

            // Sync: table scroll → top scrollbar
            const onTableScroll = () => {
                if (syncing.current || !topScrollRef.current || !tableScrollElRef.current) return;
                syncing.current = true;
                topScrollRef.current.scrollLeft = tableScrollElRef.current.scrollLeft;
                requestAnimationFrame(() => {
                    syncing.current = false;
                });
            };

            scrollContainer.addEventListener("scroll", onTableScroll, { passive: true });
            cleanupScrollListener = () => {
                scrollContainer!.removeEventListener("scroll", onTableScroll);
            };
        };

        // Cloudscape renders async — use MutationObserver to detect when the table appears
        const mutationObserver = new MutationObserver(() => {
            findAndBind();
        });
        mutationObserver.observe(wrapper, { childList: true, subtree: true });

        // Also try immediately and after delays
        findAndBind();
        const t1 = setTimeout(findAndBind, 300);
        const t2 = setTimeout(findAndBind, 1000);
        const t3 = setTimeout(findAndBind, 3000);

        // Recalculate on resize
        const resizeObserver = new ResizeObserver(findAndBind);
        resizeObserver.observe(wrapper);

        return () => {
            mutationObserver.disconnect();
            resizeObserver.disconnect();
            clearTimeout(t1);
            clearTimeout(t2);
            clearTimeout(t3);
            if (cleanupScrollListener) cleanupScrollListener();
        };
    }, []);

    // Sync: top scrollbar → table scroll
    const handleTopScroll = useCallback(() => {
        if (syncing.current || !tableScrollElRef.current || !topScrollRef.current) return;
        syncing.current = true;
        tableScrollElRef.current.scrollLeft = topScrollRef.current.scrollLeft;
        requestAnimationFrame(() => {
            syncing.current = false;
        });
    }, []);

    return (
        <div ref={wrapperRef}>
            {/* Top scrollbar — always rendered, shown when table is wider than viewport */}
            <div
                ref={topScrollRef}
                onScroll={handleTopScroll}
                style={{
                    overflowX: "auto",
                    overflowY: "hidden",
                    height: scrollInfo.visible ? "14px" : "0px",
                    borderBottom: scrollInfo.visible
                        ? "1px solid var(--vams-border-default)"
                        : "none",
                }}
            >
                <div style={{ width: scrollInfo.width || 1, height: "1px" }} />
            </div>
            {children}
        </div>
    );
};

/**
 * Renders text truncated to maxLength with a native title tooltip showing full text on hover.
 * Used for long values like file paths, descriptions, and tags.
 */
const TruncatedCell: React.FC<{
    text: string;
    maxLength?: number;
    isLink?: boolean;
    href?: string;
    onFollow?: (event: any) => void;
}> = ({ text, maxLength = 60, isLink, href, onFollow }) => {
    if (!text) return <span>-</span>;
    const isTruncated = text.length > maxLength;
    const displayText = isTruncated ? text.substring(0, maxLength) + "\u2026" : text;

    if (isLink && href) {
        return (
            <span title={isTruncated ? text : undefined}>
                <Link href={href} onFollow={onFollow}>
                    {displayText}
                </Link>
            </span>
        );
    }

    return <span title={isTruncated ? text : undefined}>{displayText}</span>;
};

function columnRender(
    e: any,
    name: string,
    value: any,
    navigate?: any,
    isFileMode?: boolean,
    onViewFile?: (item: any) => void,
    viewerRegistryReady?: boolean
) {
    // Check if item is archived
    const isArchived = e.bool_archived === true || e.status === "archived";

    if (name === "str_databaseid") {
        // Database link always remains clickable
        return (
            <Box>
                <Link href={`#/databases/${e["str_databaseid"]}/assets/`}>{value}</Link>
            </Box>
        );
    }
    if (name === "str_assetname") {
        // Remove hyperlink for archived assets
        if (isArchived) {
            return (
                <Box>
                    <SpaceBetween direction="horizontal" size="xs">
                        {value}
                        {/* Only show explanation in asset mode, not file mode */}
                        {e.explanation && !isFileMode && (
                            <ExplanationPopover explanation={e.explanation} />
                        )}
                    </SpaceBetween>
                </Box>
            );
        } else {
            return (
                <Box>
                    <SpaceBetween direction="horizontal" size="xs">
                        <Link
                            href={`#/databases/${e["str_databaseid"]}/assets/${e["str_assetid"]}`}
                        >
                            {value}
                        </Link>
                        {/* Only show explanation in asset mode, not file mode */}
                        {e.explanation && !isFileMode && (
                            <ExplanationPopover explanation={e.explanation} />
                        )}
                    </SpaceBetween>
                </Box>
            );
        }
    } else if (name === "str_key") {
        // File path — always show full path, allow wrapping
        const pathStyle: React.CSSProperties = {
            wordBreak: "break-all",
            whiteSpace: "normal",
            lineHeight: "1.4",
            fontSize: "13px",
        };
        // A horizontal SpaceBetween is a WRAPPING flex container, so a path long enough to wrap
        // pushed the eye icon onto a line of its own. This row keeps the icon and the path on one
        // line: the icon does not shrink, and the path is the only flexible item, so the text wraps
        // inside its own column with the icon pinned to the first line beside it.
        const rowStyle: React.CSSProperties = {
            display: "flex",
            flexWrap: "nowrap",
            alignItems: "flex-start",
            gap: "4px",
        };
        const iconStyle: React.CSSProperties = { flexShrink: 0, lineHeight: "1.4" };
        const pathCellStyle: React.CSSProperties = { ...pathStyle, flex: 1, minWidth: 0 };
        // Per-row eye icon: opens the popup viewer for this single file directly
        // from the search table (no need to navigate into the asset). Only shown in
        // file mode when a viewer plugin can render the file's extension.
        const viewLabel = `Visualize File ${value || e.str_fileext || ""}`.trim();
        const viewButton =
            isFileMode &&
            onViewFile &&
            viewerRegistryReady &&
            isViewableExtension(e.str_fileext) ? (
                <Button
                    variant="icon"
                    iconSvg={EYE_ICON_SVG}
                    ariaLabel={viewLabel}
                    onClick={(event) => {
                        event.stopPropagation();
                        onViewFile(e);
                    }}
                />
            ) : null;
        if (isFileMode && navigate && !isArchived) {
            // Encode the file path in BOTH the href (`?filePath=`) and the
            // router state. The href is what right-click → "Open in new
            // tab" copies, so it must carry enough information to reach
            // the target file on a fresh page load. The state remains
            // for the in-app left-click path so we don't have to re-parse
            // the URL on the receiving side.
            const filePathQuery = `?filePath=${encodeURIComponent(value)}`;
            return (
                <Box>
                    <div style={rowStyle}>
                        {viewButton && (
                            <span style={iconStyle} title={viewLabel}>
                                {viewButton}
                            </span>
                        )}
                        <span style={pathCellStyle}>
                            <Link
                                href={`#/databases/${e["str_databaseid"]}/assets/${e["str_assetid"]}${filePathQuery}`}
                                onFollow={(event) => {
                                    event.preventDefault();
                                    navigate(
                                        `/databases/${e["str_databaseid"]}/assets/${e["str_assetid"]}${filePathQuery}`,
                                        {
                                            state: { filePathToNavigate: value },
                                        }
                                    );
                                }}
                            >
                                {value}
                            </Link>
                        </span>
                        {e.explanation && (
                            <span style={iconStyle}>
                                <ExplanationPopover explanation={e.explanation} />
                            </span>
                        )}
                    </div>
                </Box>
            );
        } else {
            return (
                <Box>
                    <div style={rowStyle}>
                        {viewButton && (
                            <span style={iconStyle} title={viewLabel}>
                                {viewButton}
                            </span>
                        )}
                        <span style={pathCellStyle}>{value}</span>
                        {e.explanation && isFileMode && (
                            <span style={iconStyle}>
                                <ExplanationPopover explanation={e.explanation} />
                            </span>
                        )}
                    </div>
                </Box>
            );
        }
    } else if (name === "list_tags" && Array.isArray(value)) {
        const tagsWithType = value.map((tag) => {
            if (tagTypes)
                for (const tagType of tagTypes) {
                    let tagTypeName = tagType.tagTypeName;

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

        const tagsText = tagsWithType.join(", ");
        return (
            <Box>
                <span style={{ whiteSpace: "normal", wordBreak: "break-word" }}>
                    <TruncatedCell text={tagsText} maxLength={60} />
                </span>
            </Box>
        );
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
    } else if (name === "str_description") {
        const description = String(value ?? "");
        if (!description) {
            return (
                <Box>
                    <span>-</span>
                </Box>
            );
        }
        // Descriptions wrap onto as many lines as they need instead of being cut off. The table is
        // rendered with wrapLines={false}, which sets nowrap on an ancestor, so white-space is
        // overridden here on the element holding the text; wordBreak keeps an unbroken string from
        // overflowing the column.
        return (
            <Box>
                <div style={{ whiteSpace: "normal", wordBreak: "break-word" }}>{description}</div>
            </Box>
        );
    } else if (name.indexOf("str") === 0 || name.indexOf("num_") === 0) {
        return <Box>{value}</Box>;
    }
}

/**
 * Declared column widths, keyed by column id.
 *
 * A width must not vary with the record type. A resizable column's width is seeded once — on the
 * render its id first becomes visible — and every later declaration for that id is ignored, so an id
 * declaring one width in asset mode and another in file mode keeps whichever mode happened to render
 * first, for as long as the table stays mounted. Headers and cell renderers may still differ per
 * mode; only the widths have to agree, which is why they all resolve through this one table.
 *
 * The table uses a fixed layout, so these widths are honored as declared and the table scrolls
 * horizontally rather than squeezing a column toward its minWidth.
 */
const COLUMN_WIDTHS: Record<string, { width: number; minWidth: number }> = {
    // A full asset-relative path is the widest value in the file-mode table, and the eye icon shares
    // the cell, so this column gets the most room.
    str_key: { width: 400, minWidth: 200 },
    str_asset: { width: 180, minWidth: 120 },
    str_assetname: { width: 180, minWidth: 120 },
    str_databaseid: { width: 150, minWidth: 100 },
    str_assettype: { width: 120, minWidth: 80 },
    list_tags: { width: 150, minWidth: 100 },
    // Description wraps rather than truncating, so it is given room for a couple of lines.
    str_description: { width: 230, minWidth: 120 },
    str_asset_version_id: { width: 180, minWidth: 130 },
    str_assetversionid: { width: 180, minWidth: 130 },
    num_filesize: { width: 100, minWidth: 70 },
    num_size: { width: 100, minWidth: 70 },
    // Client-side thumbnail columns. Each is declared from a separate asset-mode and file-mode
    // branch below, so they resolve here rather than being written out twice.
    preview: { width: 150, minWidth: 100 },
    mapThumbnail: { width: 230, minWidth: 220 },
};
const DATE_COLUMN_WIDTH = { width: 160, minWidth: 120 };
const FALLBACK_COLUMN_WIDTH = { width: 150, minWidth: 100 };

/** Resolves a column id to its declared width, by id first and then by field-name convention. */
const columnWidthFor = (name: string): { width: number; minWidth: number } =>
    COLUMN_WIDTHS[name] ?? (name.startsWith("date_") ? DATE_COLUMN_WIDTH : FALLBACK_COLUMN_WIDTH);

function SearchPageListView({ state, dispatch, onShowToast }: SearchPageViewProps) {
    // Guarantees a re-render once the viewer registry is ready. The container kicks initialization
    // off in an effect, so a render that happens first would see no compatible viewers and omit
    // every eye icon; today that self-corrects only because results arrive afterwards.
    const viewerRegistryReady = useViewerRegistryReady();
    // identify all the names of columns from state.result.hits.hits
    // create a column definition for each column
    const [showDeleteModal, setShowDeleteModal] = useState(false);
    const [showUnarchiveModal, setShowUnarchiveModal] = useState(false);
    const [showPreviewModal, setShowPreviewModal] = useState(false);
    const [previewAsset, setPreviewAsset] = useState<{
        url?: string;
        assetId?: string;
        databaseId?: string;
        previewKey?: string;
        assetName?: string;
        downloadType?: "assetPreview" | "assetFile";
    }>({});
    const [viewerFiles, setViewerFiles] = useState<FileInfo[]>([]);
    const [showViewerModal, setShowViewerModal] = useState(false);

    const openViewer = (files: FileInfo[]) => {
        const viewable = files.filter((f) => !!f.key);
        if (viewable.length === 0) {
            onShowToast?.("Nothing to preview", "No selected files can be visualized");
            return;
        }
        setViewerFiles(viewable);
        setShowViewerModal(true);
    };

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

        if (
            typeof downloadTypeOrItem === "string" &&
            (downloadTypeOrItem === "assetPreview" || downloadTypeOrItem === "assetFile")
        ) {
            // New signature: downloadType passed, item might be in 5th param
            downloadType = downloadTypeOrItem;
            item = itemData;
        } else if (downloadTypeOrItem && typeof downloadTypeOrItem === "object") {
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

    // Determine if we're in file mode.
    //
    // This prefers the container's recordType over the _rectype filter because the filter is written
    // from an effect, one render after recordType changes, while the visible column list switches to
    // the file set in the same render. Reading the lagging value left one render in which str_key was
    // already visible but fell through to the generic ~150px branch below. A resizable column's
    // width is seeded exactly once — on the render it first appears — so that stale 150px stuck
    // permanently and every later declaration was ignored.
    const isFileMode = (state.recordType ?? state.filters._rectype.value) === "file";

    // A multi-file viewer has to support every selected extension, so a mixed selection no single
    // viewer covers cannot be opened. Recomputed when the registry finishes initializing, because
    // until then it reports nothing as viewable.
    const viewerSelectionFilenames: string[] = (state.viewerSelection || []).map(
        (file: any) => file?.filename || file?.key || ""
    );
    const selectionHasViewer =
        viewerSelectionFilenames.length > 0 &&
        viewerRegistryReady &&
        areFilenamesViewableTogether(viewerSelectionFilenames);

    // Determine if unarchive button should be shown (single archived asset selected)
    const showUnarchiveButton =
        state?.selectedItems?.length === 1 &&
        (state?.selectedItems[0]?.bool_archived === true ||
            state?.selectedItems[0]?.status === "archived");

    // Determine if any archived assets are selected (for delete modal)
    const hasArchivedAssetSelected = state?.selectedItems?.some(
        (item: any) => item.bool_archived === true || item.status === "archived"
    );

    // Filter out undefined/null column names and add preview column if showPreviewThumbnails is enabled
    let enhancedColumnDefinitions = orderedColumnNames
        ?.filter((name: string) => name)
        ?.map((name: string) => {
            // Custom headers based on record type

            // Both the legacy `str_asset` field and the `str_assetname` field the result documents
            // actually carry render as the asset column.
            if (name === "str_asset" || name === "str_assetname") {
                return {
                    id: name,
                    header: Synonyms.Asset,
                    cell: (e: any) => columnRender(e, name, e[name], navigate, isFileMode),
                    sortingField: name,
                    isRowHeader: false,
                    ...columnWidthFor(name),
                };
            }
            if (name === "str_databaseid") {
                return {
                    id: name,
                    header: Synonyms.Database,
                    cell: (e: any) => columnRender(e, name, e[name], navigate, isFileMode),
                    sortingField: name,
                    isRowHeader: false,
                    ...columnWidthFor(name),
                };
            }
            if (name === "str_assettype") {
                return {
                    id: name,
                    header: isFileMode ? `${Synonyms.Asset} Type` : "Type",
                    cell: (e: any) => columnRender(e, name, e[name], navigate, isFileMode),
                    sortingField: name,
                    isRowHeader: false,
                    ...columnWidthFor(name),
                };
            }
            if (name === "list_tags") {
                return {
                    id: name,
                    header: isFileMode ? `${Synonyms.Asset} Tags` : "Tags",
                    cell: (e: any) => columnRender(e, name, e[name], navigate, isFileMode),
                    sortingField: name,
                    isRowHeader: false,
                    ...columnWidthFor(name),
                };
            }
            if (name === "str_key" && isFileMode) {
                return {
                    id: name,
                    header: "File Path",
                    cell: (e: any) =>
                        columnRender(
                            e,
                            name,
                            e[name],
                            navigate,
                            isFileMode,
                            (item: any) => openViewer([searchRowToFileInfo(item)]),
                            viewerRegistryReady
                        ),
                    sortingField: name,
                    isRowHeader: false,
                    ...columnWidthFor(name),
                };
            }
            if (name === "str_description" && isFileMode) {
                return {
                    id: name,
                    header: `${Synonyms.Asset} Description`,
                    cell: (e: any) => columnRender(e, name, e[name], navigate, isFileMode),
                    sortingField: name,
                    isRowHeader: false,
                    ...columnWidthFor(name),
                };
            }

            // Use FIELD_MAPPINGS label if available, with overrides for brevity
            const shortLabels: Record<string, string> = {
                str_assetname: Synonyms.Asset,
                str_asset_version_id: "Version",
                metadata: "Metadata",
            };

            const fieldLabel =
                shortLabels[name] ||
                (FIELD_MAPPINGS as any)[name]?.label ||
                name
                    .split("_")
                    .slice(1)
                    .map((s: string) => s.charAt(0).toUpperCase() + s.slice(1))
                    .join(" ");

            return {
                id: name,
                header: fieldLabel,
                cell: (e: any) => {
                    if (name === "metadata") {
                        const { metadata, attributes } = extractMetadata(e);
                        // console.log("[MetadataDebug] Extracted data:", {
                        //     metadata,
                        //     attributes,
                        //     allKeys: Object.keys(e).filter(
                        //         (k) =>
                        //             k.toUpperCase().startsWith("MD_") ||
                        //             k.toUpperCase().startsWith("AB_")
                        //     ),
                        // });
                        return <MetadataPopover metadata={metadata} attributes={attributes} />;
                    }
                    return columnRender(e, name, e[name], navigate, isFileMode);
                },
                sortingField: name === "metadata" ? undefined : name,
                isRowHeader: false,
                ...columnWidthFor(name),
            };
        });

    // No need to rearrange columns - they should already be in the correct order from preferences

    // Add or remove preview column based on showPreviewThumbnails toggle
    if (state.showPreviewThumbnails) {
        // Remove any existing preview columns first to avoid duplicates
        enhancedColumnDefinitions = enhancedColumnDefinitions.filter(
            (col: any) => col.id !== "preview"
        );

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
                            previewFileKey={
                                item.str_previewfilekey !== undefined
                                    ? item.str_previewfilekey
                                    : undefined
                            }
                        />
                    ),
                    sortingField: undefined, // Not sortable - client-side column
                    isRowHeader: false,
                    ...columnWidthFor("preview"),
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
                            previewFileKey={
                                item.str_previewfilekey !== undefined
                                    ? item.str_previewfilekey
                                    : undefined
                            }
                        />
                    ),
                    sortingField: undefined, // Not sortable - client-side column
                    isRowHeader: false,
                    ...columnWidthFor("preview"),
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
        enhancedColumnDefinitions = enhancedColumnDefinitions.filter(
            (col: any) => col.id !== "preview"
        );

        // Remove preview from visible columns
        if (state.tablePreferences?.visibleContent) {
            state.tablePreferences.visibleContent = state.tablePreferences.visibleContent.filter(
                (col: string) => col !== "preview"
            );
        }
    }

    // Add or remove map thumbnail column based on showMapThumbnails toggle
    // Available for both asset and file results when maps are enabled
    if (state.showMapThumbnails && state.useMapView) {
        const config = appCache.getItem("config");
        const mapStyleUrl = config?.locationServiceApiUrl;

        // Remove any existing map thumbnail columns first to avoid duplicates
        enhancedColumnDefinitions = enhancedColumnDefinitions.filter(
            (col: any) => col.id !== "mapThumbnail"
        );

        if (mapStyleUrl) {
            // Add map thumbnail column after preview column (or at start if no preview)
            const insertIndex = state.showPreviewThumbnails ? 1 : 0;
            enhancedColumnDefinitions.splice(insertIndex, 0, {
                id: "mapThumbnail",
                header: "Map",
                cell: (item: any) => {
                    const source = item?._source ?? item;
                    const isFile = source?._rectype === "file";
                    const expandHeader =
                        source?.str_assetname ||
                        (isFile ? source?.str_key : undefined) ||
                        "Map preview";
                    // Stable id used to stagger polygon colors so adjacent rows differ
                    // even when each row only contains a single polygon.
                    const colorKey =
                        item?._id ||
                        source?.str_assetid ||
                        source?.str_key ||
                        source?.str_databaseid;
                    return (
                        <MapThumbnail
                            assetData={item}
                            mapStyleUrl={mapStyleUrl}
                            width={200}
                            height={150}
                            expandHeader={expandHeader}
                            colorKey={colorKey}
                        />
                    );
                },
                sortingField: undefined, // Not sortable - client-side column
                isRowHeader: false,
                ...columnWidthFor("mapThumbnail"),
            });

            // Add mapThumbnail to visible columns if not already there
            if (
                state.tablePreferences?.visibleContent &&
                !state.tablePreferences.visibleContent.includes("mapThumbnail")
            ) {
                const insertIndex = state.showPreviewThumbnails ? 1 : 0;
                state.tablePreferences.visibleContent.splice(insertIndex, 0, "mapThumbnail");
            }
        }
    } else {
        // Remove map thumbnail column when toggle is off or not in asset mode
        enhancedColumnDefinitions = enhancedColumnDefinitions.filter(
            (col: any) => col.id !== "mapThumbnail"
        );

        // Remove mapThumbnail from visible columns
        if (state.tablePreferences?.visibleContent) {
            state.tablePreferences.visibleContent = state.tablePreferences.visibleContent.filter(
                (col: string) => col !== "mapThumbnail"
            );
        }
    }

    const totalResults = getTotalResultCount(state?.result);
    const currentPage = 1 + Math.floor(state?.pagination?.from / state?.tablePreferences?.pageSize);
    const pageCount = Math.ceil(totalResults / state?.tablePreferences?.pageSize);

    console.log("[SearchPageListView] Pagination calculation:", {
        from: state?.pagination?.from,
        pageSize: state?.tablePreferences?.pageSize,
        currentPage,
        pageCount,
        totalResults,
        hitsTotal: state?.result?.hits?.total?.value,
        aggregationTotal: state?.result?.aggregationTotal,
    });

    if (!enhancedColumnDefinitions) {
        return <div>Loading...</div>;
    }

    // Debug logging
    console.log("SearchPageListView render:", {
        tableSort: state.tableSort,
        sortingField: state?.tableSort?.sortingField,
        sortingDescending: state?.tableSort?.sortingDescending,
        selectedItems: state?.selectedItems,
        selectedCount: state?.selectedItems?.length,
        showUnarchiveButton,
        hasArchivedAssetSelected,
    });

    return (
        <>
            <SpaceBetween direction="vertical" size="l">
                <Table
                    resizableColumns={true}
                    stickyHeader={true}
                    wrapLines={false}
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
                            // Viewer-selection spans searches: a file picked from an earlier result
                            // set stays selected so several searches can be combined into one
                            // viewer session. Within the CURRENT result set the checkboxes remain
                            // authoritative (check = in, uncheck = out), so the running set is
                            // rebuilt as "everything previously selected that this result set does
                            // not contain" plus "the viewable rows checked right now". Replacing it
                            // with only the current checkboxes would drop the earlier picks the
                            // moment the first row of a new search is checked, because a new search
                            // clears the checkboxes while keeping the selection.
                            if (isFileMode && state?.viewerSelectMode) {
                                const currentRows: any[] =
                                    state?.result?.hits?.hits?.map((hit: any) => hit._source) || [];
                                state.setViewerSelection(
                                    reconcileViewerSelection(
                                        state.viewerSelection || [],
                                        currentRows,
                                        detail.selectedItems as any[]
                                    )
                                );
                            }
                        }
                    }}
                    selectionType={
                        state?.filters._rectype.value === "asset"
                            ? "multi"
                            : isFileMode && state?.viewerSelectMode
                            ? "multi"
                            : undefined
                    }
                    trackBy="_id"
                    visibleColumns={state?.tablePreferences?.visibleContent}
                    loading={state.loading}
                    loadingText="Loading"
                    items={state?.result?.hits?.hits?.map((hit: any) => ({
                        ...hit._source,
                        _id: hit._id,
                        explanation: hit.explanation,
                    }))}
                    sortingColumn={
                        state?.tableSort?.sortingField
                            ? {
                                  sortingField: state?.tableSort?.sortingField,
                              }
                            : undefined
                    }
                    sortingDescending={!!state?.tableSort?.sortingDescending}
                    onSortingChange={({ detail }) => {
                        console.log("[Sort] onSortingChange detail:", detail);
                        const sortingField = detail.sortingColumn?.sortingField;
                        if (sortingField) {
                            // Send field name as-is without .keyword suffix
                            const isDescending = detail.isDescending ?? false;

                            const sort = [
                                {
                                    field: sortingField,
                                    order: isDescending ? "desc" : "asc",
                                },
                            ];

                            const tableSort = {
                                sortingField,
                                sortingDescending: isDescending,
                            };

                            console.log("[Sort] Built sort:", sort, "tableSort:", tableSort);

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
                                        from:
                                            (detail.currentPageIndex - 1) *
                                            state?.tablePreferences?.pageSize,
                                        size: state?.tablePreferences?.pageSize,
                                    },
                                });
                            }}
                        />
                    }
                    preferences={null as any}
                    header={
                        <Header
                            children={
                                state.filters._rectype.value === "file" ? "Files" : Synonyms.Assets
                            }
                            counter={
                                totalResults
                                    ? `${totalResults.toLocaleString()}${
                                          state?.result?.hits?.total?.relation === "gte" ? "+" : ""
                                      }`
                                    : ""
                            }
                            info={
                                totalResults ? (
                                    <Popover
                                        header="About this count"
                                        content="This total is based on records accessible with your current permissions. The actual number of records in the system may be higher."
                                        triggerType="custom"
                                    >
                                        <Box
                                            color="text-status-info"
                                            fontSize="body-s"
                                            display="inline"
                                        >
                                            <span
                                                style={{
                                                    cursor: "help",
                                                    textDecoration: "underline dotted",
                                                }}
                                            >
                                                &#9432;
                                            </span>
                                        </Box>
                                    </Popover>
                                ) : undefined
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
                                        {showUnarchiveButton && (
                                            <Button
                                                onClick={() => {
                                                    setShowUnarchiveModal(true);
                                                }}
                                            >
                                                Unarchive Selected
                                            </Button>
                                        )}
                                        <Button
                                            onClick={(e) => {
                                                navigate("/upload");
                                            }}
                                            variant="primary"
                                        >
                                            Create {Synonyms.Asset}
                                        </Button>
                                    </SpaceBetween>
                                ) : isFileMode ? (
                                    !state.viewerSelectMode ? (
                                        <Button onClick={() => state.enterViewerSelectMode()}>
                                            Multi-select to view
                                        </Button>
                                    ) : (
                                        <SpaceBetween direction="horizontal" size="xs">
                                            <span
                                                title={
                                                    !state.viewerSelection?.length
                                                        ? undefined
                                                        : selectionHasViewer
                                                        ? undefined
                                                        : "No viewer can display this combination of file types together. Every selected file has to be supported by the same viewer."
                                                }
                                            >
                                                <Button
                                                    variant="primary"
                                                    disabled={
                                                        !state.viewerSelection?.length ||
                                                        !selectionHasViewer
                                                    }
                                                    onClick={() =>
                                                        openViewer(state.viewerSelection)
                                                    }
                                                >
                                                    View Selected (
                                                    {state.viewerSelection?.length || 0})
                                                </Button>
                                            </span>
                                            <Button
                                                disabled={!state.viewerSelection?.length}
                                                onClick={() => {
                                                    // The checkboxes are a second copy of this
                                                    // state, so both are emptied together —
                                                    // clearing only the running set left the rows
                                                    // on screen ticked while the count read 0.
                                                    state.clearViewerSelection();
                                                    dispatch({
                                                        type: "set-selected-items",
                                                        selectedItems: [],
                                                    });
                                                }}
                                            >
                                                Clear selection
                                            </Button>
                                            <Button
                                                onClick={() => {
                                                    // Leaving the mode discards the selection too,
                                                    // so the checkboxes cannot come back ticked
                                                    // against an empty set on re-entry.
                                                    state.exitViewerSelectMode();
                                                    dispatch({
                                                        type: "set-selected-items",
                                                        selectedItems: [],
                                                    });
                                                }}
                                            >
                                                Exit
                                            </Button>
                                        </SpaceBetween>
                                    )
                                ) : null
                            }
                        />
                    }
                />
            </SpaceBetween>
            <AssetDeleteModal
                visible={showDeleteModal}
                onDismiss={() => setShowDeleteModal(false)}
                mode="asset"
                selectedAssets={state?.selectedItems || []}
                forceDeleteMode={hasArchivedAssetSelected}
                onSuccess={(operation) => {
                    setShowDeleteModal(false);

                    // Clear selection
                    dispatch({
                        type: "set-selected-items",
                        selectedItems: [],
                    });

                    // Show toast notification
                    if (onShowToast) {
                        const operationName =
                            operation === "archive" ? "archived" : "permanently deleted";
                        onShowToast(
                            `${Synonyms.Asset} ${operationName} successfully`,
                            "Changes may take a few minutes to propagate throughout the system, including search results."
                        );
                    }

                    // Refresh the search results
                    search({}, { state, dispatch });
                }}
            />

            <AssetUnarchiveModal
                visible={showUnarchiveModal}
                onDismiss={() => setShowUnarchiveModal(false)}
                selectedAsset={state?.selectedItems?.[0]}
                onSuccess={() => {
                    setShowUnarchiveModal(false);

                    // Clear selection
                    dispatch({
                        type: "set-selected-items",
                        selectedItems: [],
                    });

                    // Show toast notification
                    if (onShowToast) {
                        onShowToast(
                            `${Synonyms.Asset} unarchived successfully`,
                            "Changes may take a few minutes to propagate throughout the system, including search results."
                        );
                    }

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

            {showViewerModal && viewerFiles.length > 0 && (
                <FileViewerModal
                    visible={showViewerModal}
                    files={viewerFiles}
                    databaseId={viewerFiles[0].databaseId || ""}
                    assetId={viewerFiles[0].assetId || ""}
                    onDismiss={() => {
                        setShowViewerModal(false);
                        setViewerFiles([]);
                    }}
                />
            )}
        </>
    );
}

export default SearchPageListView;
