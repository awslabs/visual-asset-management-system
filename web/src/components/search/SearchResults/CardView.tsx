/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import {
    Cards,
    Box,
    SpaceBetween,
    Badge,
    Link,
    Button,
    Header,
    Pagination,
    CollectionPreferences,
    Popover,
    Icon,
} from "@cloudscape-design/components";
import { SearchResult, FIELD_MAPPINGS, SearchExplanation } from "../types";
import PreviewThumbnailCell from "../SearchPreviewThumbnail/PreviewThumbnailCell";
import FilePreviewThumbnailCell from "../SearchPreviewThumbnail/FilePreviewThumbnailCell";
import Synonyms from "../../../synonyms";
import { formatFileSizeForDisplay } from "../../../common/utils/fileSize";

// Helper function to extract and format metadata and attribute fields with type information
const extractMetadata = (
    item: SearchResult
): {
    metadata: Array<{ name: string; type: string; value: any }>;
    attributes: Array<{ name: string; type: string; value: any }>;
} => {
    const metadata: Array<{ name: string; type: string; value: any }> = [];
    const attributes: Array<{ name: string; type: string; value: any }> = [];
    const source = item._source;

    // Type mapping for display
    const typeLabels: Record<string, string> = {
        str: "String",
        num: "Number",
        bool: "Boolean",
        date: "Date",
        list: "List",
        gp: "Geo Point",
        gs: "Geo Shape",
    };

    // Find all MD_ and AB_ fields (case-insensitive)
    Object.keys(source).forEach((key) => {
        const keyUpper = key.toUpperCase();

        if (keyUpper.startsWith("MD_")) {
            // Format: MD_<type>_<fieldname> or md_<fieldname>
            const parts = key.split("_");
            if (parts.length >= 3) {
                // parts[0] = 'MD'/'md', parts[1] = type, parts[2+] = field name
                const fieldType = parts[1].toLowerCase();
                const fieldName = parts.slice(2).join("_");
                metadata.push({
                    name: fieldName,
                    type: typeLabels[fieldType] || fieldType,
                    value: source[key],
                });
            } else if (parts.length === 2) {
                // Format: md_<fieldname> (no type specified)
                const fieldName = parts[1];
                // Try to infer type from value
                const value = source[key];
                let inferredType = "String";
                if (typeof value === "number") {
                    inferredType = "Number";
                } else if (typeof value === "boolean") {
                    inferredType = "Boolean";
                }
                metadata.push({
                    name: fieldName,
                    type: inferredType,
                    value: value,
                });
            } else {
                // Fallback: just remove MD_ prefix
                metadata.push({
                    name: key.substring(3),
                    type: "Unknown",
                    value: source[key],
                });
            }
        } else if (keyUpper.startsWith("AB_")) {
            // Format: AB_<type>_<fieldname> or ab_<fieldname>
            const parts = key.split("_");
            if (parts.length >= 3) {
                // parts[0] = 'AB'/'ab', parts[1] = type, parts[2+] = field name
                const fieldType = parts[1].toLowerCase();
                const fieldName = parts.slice(2).join("_");
                attributes.push({
                    name: fieldName,
                    type: typeLabels[fieldType] || fieldType,
                    value: source[key],
                });
            } else if (parts.length === 2) {
                // Format: ab_<fieldname> (no type specified)
                const fieldName = parts[1];
                // Try to infer type from value
                const value = source[key];
                let inferredType = "String";
                if (typeof value === "number") {
                    inferredType = "Number";
                } else if (typeof value === "boolean") {
                    inferredType = "Boolean";
                }
                attributes.push({
                    name: fieldName,
                    type: inferredType,
                    value: value,
                });
            } else {
                // Fallback: just remove AB_ prefix
                attributes.push({
                    name: key.substring(3),
                    type: "Unknown",
                    value: source[key],
                });
            }
        }
    });

    return { metadata, attributes };
};

// Helper component to render metadata and attributes popover
const MetadataPopover: React.FC<{
    metadata: Array<{ name: string; type: string; value: any }>;
    attributes: Array<{ name: string; type: string; value: any }>;
}> = ({ metadata, attributes }) => {
    // Don't show popover if both arrays are empty
    if (metadata.length === 0 && attributes.length === 0) {
        return null;
    }

    return (
        <Box>
            <SpaceBetween direction="horizontal" size="xs">
                <Box color="text-body-secondary" fontSize="body-s">
                    <strong>Metadata:</strong>
                </Box>
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
            </SpaceBetween>
        </Box>
    );
};

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

interface CardViewProps {
    items: SearchResult[];
    selectedItems: SearchResult[];
    onSelectionChange: (items: SearchResult[]) => void;
    loading: boolean;
    cardSize: "small" | "medium" | "large";
    showThumbnails: boolean;
    recordType: "asset" | "file";
    onOpenPreview?: (url: string, name: string, previewKey: string, item?: any) => void;
    // Pagination props
    currentPageIndex: number;
    pagesCount: number;
    onPageChange: (pageIndex: number) => void;
    // Preferences
    onPreferencesChange?: (preferences: any) => void;
    preferences?: any;
    // Actions
    onCreateAsset?: () => void;
    onDeleteSelected?: () => void;
    totalItems?: number;
}

const CardView: React.FC<CardViewProps> = ({
    items,
    selectedItems,
    onSelectionChange,
    loading,
    cardSize,
    showThumbnails,
    recordType,
    onOpenPreview,
    currentPageIndex,
    pagesCount,
    onPageChange,
    onPreferencesChange,
    preferences,
    onCreateAsset,
    onDeleteSelected,
    totalItems,
}) => {
    const formatFieldValue = (key: string, value: any) => {
        if (!value) return "-";

        const fieldMapping = FIELD_MAPPINGS[key];

        switch (fieldMapping?.type) {
            case "array":
                if (Array.isArray(value)) {
                    return value.map((item, index) => (
                        <Badge key={index} color="blue">
                            {item}
                        </Badge>
                    ));
                }
                return value;
            case "date":
                return new Date(value).toLocaleDateString();
            case "number":
                if (key === "num_filesize" || key === "num_size") {
                    // Format file size using utility
                    return formatFileSizeForDisplay(value);
                }
                return value.toLocaleString();
            default:
                return value;
        }
    };

    const renderCardContent = (item: SearchResult) => {
        const source = item._source;
        const isAsset = recordType === "asset";

        return (
            <SpaceBetween direction="vertical" size="s">
                {/* Thumbnail */}
                {showThumbnails && (
                    <Box>
                        {isAsset ? (
                            <PreviewThumbnailCell
                                assetId={source.str_assetid || ""}
                                databaseId={source.str_databaseid || ""}
                                onOpenFullPreview={onOpenPreview || (() => {})}
                                assetName={source.str_assetname || ""}
                            />
                        ) : (
                            <FilePreviewThumbnailCell
                                assetId={source.str_assetid || ""}
                                databaseId={source.str_databaseid || ""}
                                fileKey={source.str_key || ""}
                                fileName={source.str_key?.split("/").pop() || source.str_key || ""}
                                fileSize={source.num_filesize || source.num_size}
                                onOpenFullPreview={onOpenPreview || (() => {})}
                            />
                        )}
                    </Box>
                )}

                {/* Main Content */}
                <SpaceBetween direction="vertical" size="xs">
                    {/* For Assets: Show asset name as title with link */}
                    {isAsset && (
                        <Box>
                            <Link
                                href={`#/databases/${source.str_databaseid}/assets/${source.str_assetid}`}
                                fontSize="heading-s"
                            >
                                {source.str_assetname || "Unnamed Asset"}
                            </Link>
                        </Box>
                    )}

                    {/* For Files: Show database first, then asset name as link */}
                    {!isAsset && (
                        <>
                            {/* Database */}
                            <Box>
                                <Link href={`#/databases/${source.str_databaseid}/assets/`}>
                                    <Badge color="grey">{source.str_databaseid}</Badge>
                                </Link>
                            </Box>

                            {/* Asset Name as Link */}
                            {source.str_assetname && source.str_assetid && (
                                <Box>
                                    <Link
                                        href={`#/databases/${source.str_databaseid}/assets/${source.str_assetid}`}
                                        fontSize="body-m"
                                    >
                                        {source.str_assetname}
                                    </Link>
                                </Box>
                            )}
                        </>
                    )}

                    {/* Database (for assets only) */}
                    {isAsset && (
                        <Box>
                            <Link href={`#/databases/${source.str_databaseid}/assets/`}>
                                <Badge color="grey">{source.str_databaseid}</Badge>
                            </Link>
                        </Box>
                    )}

                    {/* Type */}
                    {source.str_assettype && (
                        <Box>
                            <Badge>{source.str_assettype}</Badge>
                        </Box>
                    )}

                    {/* Description (for assets) */}
                    {isAsset && source.str_description && (
                        <Box color="text-body-secondary" fontSize="body-s">
                            {source.str_description.length > 100
                                ? `${source.str_description.substring(0, 100)}...`
                                : source.str_description}
                        </Box>
                    )}

                    {/* File Path (for files) */}
                    {!isAsset && source.str_key && (
                        <Box color="text-body-secondary" fontSize="body-s">
                            <strong>Path:</strong> {source.str_key}
                        </Box>
                    )}

                    {/* File Size (for files) */}
                    {!isAsset && (source.num_filesize || source.num_size) && (
                        <Box color="text-body-secondary" fontSize="body-s">
                            <strong>Size:</strong>{" "}
                            {formatFieldValue(
                                "num_filesize",
                                source.num_filesize || source.num_size
                            )}
                        </Box>
                    )}

                    {/* Tags (for files, show below size) */}
                    {!isAsset &&
                        source.list_tags &&
                        Array.isArray(source.list_tags) &&
                        source.list_tags.length > 0 && (
                            <Box>
                                <SpaceBetween direction="horizontal" size="xs">
                                    {source.list_tags.slice(0, 3).map((tag, index) => (
                                        <Badge key={index} color="blue">
                                            {tag}
                                        </Badge>
                                    ))}
                                    {source.list_tags.length > 3 && (
                                        <Badge color="grey">
                                            +{source.list_tags.length - 3} more
                                        </Badge>
                                    )}
                                </SpaceBetween>
                            </Box>
                        )}

                    {/* Tags (for assets, show in original position) */}
                    {isAsset &&
                        source.list_tags &&
                        Array.isArray(source.list_tags) &&
                        source.list_tags.length > 0 && (
                            <Box>
                                <SpaceBetween direction="horizontal" size="xs">
                                    {source.list_tags.slice(0, 3).map((tag, index) => (
                                        <Badge key={index} color="blue">
                                            {tag}
                                        </Badge>
                                    ))}
                                    {source.list_tags.length > 3 && (
                                        <Badge color="grey">
                                            +{source.list_tags.length - 3} more
                                        </Badge>
                                    )}
                                </SpaceBetween>
                            </Box>
                        )}

                    {/* Metadata Info */}
                    {(() => {
                        const { metadata, attributes } = extractMetadata(item);
                        return <MetadataPopover metadata={metadata} attributes={attributes} />;
                    })()}

                    {/* Created Info */}
                    {(source.date_created || source.str_createdby) && (
                        <Box color="text-body-secondary" fontSize="body-s">
                            {source.date_created && (
                                <>
                                    Created: {formatFieldValue("date_created", source.date_created)}
                                </>
                            )}
                            {source.str_createdby && <> by {source.str_createdby}</>}
                        </Box>
                    )}
                </SpaceBetween>
            </SpaceBetween>
        );
    };

    const cardSizeMap = {
        small: { minWidth: "200px", maxWidth: "250px" },
        medium: { minWidth: "280px", maxWidth: "320px" },
        large: { minWidth: "350px", maxWidth: "400px" },
    };

    return (
        <Cards
            cardDefinition={{
                header: (item: SearchResult) => {
                    const source = item._source;
                    const headerText =
                        recordType === "asset"
                            ? source.str_assetname || "Unnamed Asset"
                            : source.str_key?.split("/").pop() || "Unnamed File";

                    // Show explanation icon if available
                    if (item.explanation) {
                        return (
                            <SpaceBetween direction="horizontal" size="xs">
                                <span>{headerText}</span>
                                <ExplanationPopover explanation={item.explanation} />
                            </SpaceBetween>
                        );
                    }

                    return headerText;
                },
                sections: [
                    {
                        content: renderCardContent,
                    },
                ],
            }}
            cardsPerRow={[
                { cards: cardSize === "large" ? 2 : cardSize === "medium" ? 3 : 4 },
                { minWidth: 500, cards: cardSize === "large" ? 3 : cardSize === "medium" ? 4 : 5 },
                { minWidth: 800, cards: cardSize === "large" ? 4 : cardSize === "medium" ? 5 : 6 },
            ]}
            items={items}
            loading={loading}
            loadingText="Loading results..."
            selectedItems={selectedItems}
            selectionType={recordType === "asset" ? "multi" : undefined}
            onSelectionChange={({ detail }) => onSelectionChange(detail.selectedItems)}
            trackBy="_id"
            empty={
                <Box textAlign="center" color="inherit">
                    <Box variant="strong" textAlign="center" color="inherit">
                        No matches
                    </Box>
                    <Box variant="p" padding={{ bottom: "s" }} color="inherit">
                        We can't find a match.
                    </Box>
                    <Button onClick={() => window.location.reload()}>Clear filter</Button>
                </Box>
            }
            header={
                <Header
                    counter={totalItems ? `(${totalItems})` : ""}
                    actions={
                        <SpaceBetween direction="horizontal" size="xs">
                            {recordType === "asset" && (
                                <Button
                                    disabled={selectedItems.length === 0}
                                    onClick={onDeleteSelected}
                                >
                                    Delete Selected
                                </Button>
                            )}
                            {onCreateAsset && (
                                <Button onClick={onCreateAsset} variant="primary">
                                    Create {Synonyms.Asset}
                                </Button>
                            )}
                        </SpaceBetween>
                    }
                >
                    {recordType === "asset" ? Synonyms.Assets : "Files"}
                </Header>
            }
            pagination={
                <Pagination
                    currentPageIndex={currentPageIndex}
                    pagesCount={pagesCount}
                    onChange={({ detail }) => onPageChange(detail.currentPageIndex)}
                />
            }
            preferences={
                onPreferencesChange && (
                    <CollectionPreferences
                        title="Preferences"
                        confirmLabel="Confirm"
                        cancelLabel="Cancel"
                        preferences={preferences}
                        onConfirm={({ detail }) => onPreferencesChange(detail)}
                        pageSizePreference={{
                            title: "Page size",
                            options: [
                                { value: 10, label: "10 resources" },
                                { value: 25, label: "25 resources" },
                                { value: 50, label: "50 resources" },
                                { value: 100, label: "100 resources" },
                            ],
                        }}
                        wrapLinesPreference={{
                            label: "Wrap lines",
                            description: "Check to see all the text and wrap the lines",
                        }}
                        stripedRowsPreference={{
                            label: "Striped rows",
                            description: "Check to add alternating shaded rows",
                        }}
                        contentDensityPreference={{
                            label: "Compact mode",
                            description: "Check to display content in a denser, more compact mode",
                        }}
                    />
                )
            }
        />
    );
};

export default CardView;
