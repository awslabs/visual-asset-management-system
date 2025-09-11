import React from "react";
import { Icon } from "@cloudscape-design/components";
import { FileTree } from "../types/FileManagerTypes";
import "./FolderTreeView.css";

export interface FolderTreeViewProps {
    treeData: FileTree;
    selectedFolder: string | null;
    onFolderSelect: (folderPath: string) => void;
    expandedFolders: Set<string>;
    onToggleExpanded: (folderPath: string) => void;
}

interface FolderTreeItemProps {
    item: FileTree;
    selectedFolder: string | null;
    onFolderSelect: (folderPath: string) => void;
    expandedFolders: Set<string>;
    onToggleExpanded: (folderPath: string) => void;
}

function FolderTreeItem({
    item,
    selectedFolder,
    onFolderSelect,
    expandedFolders,
    onToggleExpanded,
}: FolderTreeItemProps) {
    // Only show folders
    const isFolder =
        item.isFolder !== undefined
            ? item.isFolder
            : item.subTree.length > 0 || item.keyPrefix.endsWith("/");

    if (!isFolder) {
        return null;
    }

    const isExpanded = expandedFolders.has(item.relativePath);
    const isSelected = selectedFolder === item.relativePath;
    const hasSubfolders = item.subTree.some((child) => {
        const childIsFolder =
            child.isFolder !== undefined
                ? child.isFolder
                : child.subTree.length > 0 || child.keyPrefix.endsWith("/");
        return childIsFolder;
    });

    const handleClick = () => {
        onFolderSelect(item.relativePath);
    };

    const handleToggleExpanded = (e: React.MouseEvent) => {
        e.stopPropagation();
        onToggleExpanded(item.relativePath);
    };

    return (
        <div className="folder-tree-item">
            <div
                className={`folder-tree-item-content ${isSelected ? "selected" : ""}`}
                style={{ paddingLeft: `${item.level * 16}px` }}
                onClick={handleClick}
            >
                {hasSubfolders && (
                    <span className="folder-tree-item-caret" onClick={handleToggleExpanded}>
                        {isExpanded ? (
                            <Icon name="caret-down-filled" />
                        ) : (
                            <Icon name="caret-right-filled" />
                        )}
                    </span>
                )}

                {!hasSubfolders && <span className="folder-tree-item-spacer" />}

                <span className="folder-tree-item-icon">
                    {isExpanded ? <Icon name="folder-open" /> : <Icon name="folder" />}
                </span>

                <span className="folder-tree-item-name">{item.displayName}</span>
            </div>

            {hasSubfolders && isExpanded && (
                <div className="folder-tree-item-children">
                    {item.subTree
                        .filter((child) => {
                            const childIsFolder =
                                child.isFolder !== undefined
                                    ? child.isFolder
                                    : child.subTree.length > 0 || child.keyPrefix.endsWith("/");
                            return childIsFolder;
                        })
                        .map((child) => (
                            <FolderTreeItem
                                key={child.keyPrefix}
                                item={child}
                                selectedFolder={selectedFolder}
                                onFolderSelect={onFolderSelect}
                                expandedFolders={expandedFolders}
                                onToggleExpanded={onToggleExpanded}
                            />
                        ))}
                </div>
            )}
        </div>
    );
}

export function FolderTreeView({
    treeData,
    selectedFolder,
    onFolderSelect,
    expandedFolders,
    onToggleExpanded,
}: FolderTreeViewProps) {
    return (
        <div className="folder-tree-view">
            <FolderTreeItem
                item={treeData}
                selectedFolder={selectedFolder}
                onFolderSelect={onFolderSelect}
                expandedFolders={expandedFolders}
                onToggleExpanded={onToggleExpanded}
            />
        </div>
    );
}
