import React, { useContext } from "react";
import { Box, Button, Icon, Spinner, TextFilter, Toggle } from "@cloudscape-design/components";
import {
    TreeItemProps,
    SearchResultsProps,
    DirectoryTreeProps,
    FileManagerContextType,
} from "../types/FileManagerTypes";
import "./FileTreeView.css";

// Create a context that will be overridden by the main component
export const FileManagerContext = React.createContext<FileManagerContextType | undefined>(
    undefined
);

// Tree Item Component
function TreeItem({ item }: TreeItemProps) {
    const context = useContext(FileManagerContext);
    if (!context) {
        throw new Error("TreeItem must be used within a FileManagerContext.Provider");
    }
    const { state, dispatch } = context;

    // Check if it's a folder by using isFolder property, or by having subTree items or if the keyPrefix ends with '/'
    const isFolder =
        item.isFolder !== undefined
            ? item.isFolder
            : item.subTree.length > 0 || item.keyPrefix.endsWith("/");
    const isSelected = state.selectedItem?.relativePath === item.relativePath;
    const isMultiSelected = state.selectedItems.some(
        (selectedItem) => selectedItem.relativePath === item.relativePath
    );

    const handleClick = (e: React.MouseEvent) => {
        dispatch({
            type: "SELECT_ITEM",
            payload: {
                item,
                ctrlKey: e.ctrlKey,
                shiftKey: e.shiftKey,
            },
        });
    };

    return (
        <div className="tree-item">
            <div
                className={`tree-item-content ${isSelected ? "selected" : ""} ${
                    isMultiSelected && state.multiSelectMode ? "multi-selected" : ""
                }`}
                style={{ paddingLeft: `${item.level * 16}px` }}
                onClick={handleClick}
            >
                {isFolder && (
                    <span
                        className="tree-item-caret"
                        onClick={(e) => {
                            e.stopPropagation();
                            dispatch({
                                type: "TOGGLE_EXPANDED",
                                payload: { relativePath: item.relativePath },
                            });
                        }}
                    >
                        {item.expanded ? (
                            <Icon name="caret-down-filled" />
                        ) : (
                            <Icon name="caret-right-filled" />
                        )}
                    </span>
                )}

                <span className="tree-item-icon">
                    {isFolder ? (
                        item.expanded ? (
                            <Icon name="folder-open" />
                        ) : (
                            <Icon name="folder" />
                        )
                    ) : (
                        <Icon name="file" />
                    )}
                </span>

                <span className="tree-item-name">
                    {item.displayName}
                    {isFolder && item.subTree.length > 0 && (
                        <span className="folder-count">({item.subTree.length})</span>
                    )}
                    {!isFolder && item.primaryType && (
                        <span className="folder-count">({item.primaryType})</span>
                    )}
                    {item.isArchived && (
                        <span className="archived-icon" title="Archived">
                            <Icon name="status-negative" />
                        </span>
                    )}
                    {/* Only show warning icon for files (not folders or top node) */}
                    {item.currentAssetVersionFileVersionMismatch && !isFolder && item.level > 0 && (
                        <span className="not-included-icon" title="Not included in Asset Version">
                            <Icon name="status-warning" />
                        </span>
                    )}
                </span>
            </div>

            {isFolder && item.expanded && (
                <div className="tree-item-children">
                    {item.subTree.map((child) => (
                        <TreeItem key={child.keyPrefix} item={child} />
                    ))}
                </div>
            )}
        </div>
    );
}

// Search Results Component
function SearchResults({}: SearchResultsProps) {
    const context = useContext(FileManagerContext);
    if (!context) {
        throw new Error("SearchResults must be used within a FileManagerContext.Provider");
    }
    const { state, dispatch } = context;

    if (state.searchResults.length === 0) {
        return (
            <Box textAlign="center" padding="m">
                <div>No files or folders match your search</div>
            </Box>
        );
    }

    return (
        <div className="search-results">
            {state.searchResults.map((item) => {
                const isFolder =
                    item.isFolder !== undefined
                        ? item.isFolder
                        : item.subTree.length > 0 || item.keyPrefix.endsWith("/");

                return (
                    <div
                        key={item.keyPrefix}
                        className={`search-result-item ${
                            state.selectedItem?.relativePath === item.relativePath ? "selected" : ""
                        } ${
                            state.selectedItems.some(
                                (selectedItem) => selectedItem.relativePath === item.relativePath
                            ) && state.multiSelectMode
                                ? "multi-selected"
                                : ""
                        }`}
                        onClick={(e) =>
                            dispatch({
                                type: "SELECT_ITEM",
                                payload: {
                                    item,
                                    ctrlKey: e.ctrlKey,
                                    shiftKey: e.shiftKey,
                                },
                            })
                        }
                    >
                        <span className="search-result-icon">
                            {isFolder ? <Icon name="folder" /> : <Icon name="file" />}
                        </span>
                        <span className="search-result-name">
                            {item.displayName}
                            {isFolder && item.subTree.length > 0 && (
                                <span className="folder-count">({item.subTree.length})</span>
                            )}
                            {!isFolder && item.primaryType && (
                                <span className="folder-count">({item.primaryType})</span>
                            )}
                            {item.isArchived && (
                                <span className="archived-icon" title="Archived">
                                    <Icon name="status-negative" />
                                </span>
                            )}
                            {/* Only show warning icon for files (not folders or top node) */}
                            {item.currentAssetVersionFileVersionMismatch &&
                                !isFolder &&
                                item.level > 0 && (
                                    <span
                                        className="not-included-icon"
                                        title="Not included in Asset Version"
                                    >
                                        <Icon name="status-warning" />
                                    </span>
                                )}
                        </span>
                        <span className="search-result-path">{item.relativePath}</span>
                    </div>
                );
            })}
        </div>
    );
}

// Directory Tree Component
export function DirectoryTree({}: DirectoryTreeProps) {
    const context = useContext(FileManagerContext);
    if (!context) {
        throw new Error("DirectoryTree must be used within a FileManagerContext.Provider");
    }
    const { state, dispatch } = context;

    // Show loading progress for streaming phases
    // Only show spinner during active loading phases, not during transition phases
    const isStreaming =
        state.loading &&
        (state.loadingPhase === "basic-loading" || state.loadingPhase === "detailed-loading");
    const loadingMessage =
        state.loadingPhase === "basic-loading"
            ? "Loading files..."
            : state.loadingPhase === "basic-complete"
            ? "Loading files..."
            : state.loadingPhase === "detailed-loading"
            ? "Loading details..."
            : "";

    return (
        <div className="directory-tree-container">
            <div className="search-box">
                <div className="search-container">
                    <TextFilter
                        filteringText={state.searchTerm}
                        filteringPlaceholder="Search files and folders"
                        filteringAriaLabel="Search files and folders"
                        onChange={({ detail }) =>
                            dispatch({
                                type: "SET_SEARCH_TERM",
                                payload: { searchTerm: detail.filteringText },
                            })
                        }
                        countText={
                            state.isSearching ? `${state.searchResults.length} matches` : undefined
                        }
                    />
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        {isStreaming ? (
                            <Spinner size="normal" />
                        ) : (
                            <Button
                                iconName="refresh"
                                variant="icon"
                                ariaLabel="Refresh files"
                                onClick={() => dispatch({ type: "REFRESH_FILES", payload: null })}
                            />
                        )}
                    </div>
                </div>
                {isStreaming && state.loadingProgress.current > 0 && (
                    <div
                        style={{
                            fontSize: "12px",
                            color: "#666",
                            marginTop: "4px",
                            display: "flex",
                            alignItems: "center",
                            gap: "8px",
                        }}
                    >
                        <span>{loadingMessage}</span>
                        <span style={{ color: "#0972d3" }}>
                            Page {state.loadingProgress.current}
                        </span>
                    </div>
                )}
            </div>

            {state.isSearching ? (
                <SearchResults />
            ) : (
                <div className="directory-tree">
                    <TreeItem item={state.fileTree} />
                </div>
            )}

            <div className="selection-controls">
                <div className="selection-note">Hold Ctrl or Shift to select multiple files</div>
                <div className="toggle-controls">
                    <div className="toggle-row">
                        <div className="archived-toggle">
                            <Toggle
                                onChange={({ detail }) =>
                                    dispatch({
                                        type: "TOGGLE_SHOW_ARCHIVED",
                                        payload: null,
                                    })
                                }
                                checked={state.showArchived}
                            >
                                Show archived files
                            </Toggle>
                        </div>
                        <div className="non-included-toggle">
                            <Toggle
                                onChange={({ detail }) =>
                                    dispatch({
                                        type: "TOGGLE_SHOW_NON_INCLUDED",
                                        payload: null,
                                    })
                                }
                                checked={state.showNonIncluded}
                            >
                                Filter for non-included
                            </Toggle>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
