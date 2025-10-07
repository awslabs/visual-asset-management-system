/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export interface AssetNode {
    assetId: string;
    assetName: string;
    databaseId: string;
    assetLinkId: string;
    assetLinkAliasId?: string;
    metadata?: AssetLinkMetadata[];
}

export interface AssetTreeNode extends AssetNode {
    children?: AssetTreeNode[];
}

export interface AssetLinksData {
    related: AssetNode[];
    parents: AssetNode[];
    children: AssetNode[] | AssetTreeNode[];
    unauthorizedCounts?: {
        related: number;
        parents: number;
        children: number;
    };
    message?: string;
}

export interface AssetLinkMetadata {
    assetLinkId: string;
    metadataKey: string;
    metadataValue: string;
    metadataValueType:
        | "xyz"
        | "wxyz"
        | "string"
        | "number"
        | "matrix4x4"
        | "geopoint"
        | "geojson"
        | "lla"
        | "json"
        | "date"
        | "boolean";
}

export interface XYZValue {
    x: number;
    y: number;
    z: number;
}

export interface AssetLinkMetadataState {
    metadata: AssetLinkMetadata[];
    loading: boolean;
    error: string | null;
}

export interface TreeNodeItem {
    id: string;
    name: string;
    type: "root" | "asset";
    relationshipType?: "related" | "parent" | "child";
    assetData?: AssetNode | AssetTreeNode;
    children: TreeNodeItem[];
    expanded: boolean;
    level: number;
}

export interface AssetLinksState {
    treeData: TreeNodeItem[];
    selectedNode: TreeNodeItem | null;
    showChildrenSubTree?: boolean; // Optional for upload mode
    showTagsInTree?: boolean; // Toggle for showing tags in tree view
    loading?: boolean; // Optional for upload mode
    error: string | null;
    assetLinksData: AssetLinksData | null;
    metadata: AssetLinkMetadataState;
    searchTerm: string;
    searchResults: TreeNodeItem[];
    isSearching: boolean;
    // Optional metadata change handler for upload mode
    onAssetLinkMetadataChange?: (
        assetId: string,
        relationshipType: "related" | "parent" | "child",
        metadata: AssetLinkMetadata[]
    ) => void;
    // Cache for asset details to avoid repeated API calls
    assetDetailsCache?: { [key: string]: any };
}

export interface AssetLinksContextType {
    state: AssetLinksState;
    dispatch: React.Dispatch<AssetLinksAction>;
}

export type AssetLinksAction =
    | { type: "SET_LOADING"; payload: boolean }
    | { type: "SET_ERROR"; payload: string | null }
    | { type: "SET_ASSET_LINKS_DATA"; payload: AssetLinksData }
    | { type: "SELECT_NODE"; payload: TreeNodeItem | null }
    | { type: "TOGGLE_NODE_EXPANDED"; payload: string }
    | { type: "TOGGLE_CHILDREN_SUBTREE"; payload: null }
    | { type: "TOGGLE_TAGS_IN_TREE"; payload: null }
    | { type: "REFRESH_DATA"; payload: null }
    | { type: "SET_METADATA_LOADING"; payload: boolean }
    | { type: "SET_METADATA_ERROR"; payload: string | null }
    | { type: "SET_METADATA"; payload: AssetLinkMetadata[] }
    | { type: "ADD_METADATA"; payload: AssetLinkMetadata }
    | { type: "UPDATE_METADATA"; payload: AssetLinkMetadata }
    | { type: "DELETE_METADATA"; payload: { assetLinkId: string; metadataKey: string } }
    | { type: "SET_SEARCH_TERM"; payload: { searchTerm: string } }
    | { type: "SET_SEARCH_RESULTS"; payload: { searchResults: TreeNodeItem[] } }
    | { type: "SET_ASSET_DETAILS"; payload: { assetId: string; details: any } };

export interface AssetLinksTabProps {
    // Mode determines behavior
    mode: "view" | "upload";

    // For view mode (existing assets)
    assetId?: string;
    databaseId?: string;
    isActive?: boolean;

    // For upload mode (new assets)
    setValid?: (validity: boolean) => void;
    showErrors?: boolean;
    onAssetLinksChange?: (assetLinks: NewAssetLinksData) => void;
    initialData?: NewAssetLinksData;
}

// Legacy props interface for backward compatibility
export interface LegacyAssetLinksTabProps {
    assetId: string;
    databaseId: string;
    isActive: boolean;
}

export interface NewAssetLinksTabProps {
    setValid: (validity: boolean) => void;
    showErrors: boolean;
    onAssetLinksChange: (assetLinks: NewAssetLinksData) => void;
    initialData?: NewAssetLinksData;
}

export interface NewAssetLinksData {
    assetLinksFe: {
        parents: AssetNode[];
        child: AssetNode[];
        related: AssetNode[];
    };
    assetLinks: {
        parents: string[];
        child: string[];
        related: string[];
    };
    assetLinksMetadata: {
        parents: { [assetId: string]: AssetLinkMetadata[] };
        child: { [assetId: string]: AssetLinkMetadata[] };
        related: { [assetId: string]: AssetLinkMetadata[] };
    };
}

export interface NewAssetLinksState {
    treeData: TreeNodeItem[];
    selectedNode: TreeNodeItem | null;
    loading: boolean;
    error: string | null;
    initialized: boolean;
    hasChanges: boolean;
    lastInitialData: NewAssetLinksData | null;
    localAssetLinks: NewAssetLinksData;
}

export interface NewAssetLinksContextType {
    state: NewAssetLinksState;
    dispatch: React.Dispatch<NewAssetLinksAction>;
}

export type NewAssetLinksAction =
    | { type: "SET_LOADING"; payload: boolean }
    | { type: "SET_ERROR"; payload: string | null }
    | { type: "SELECT_NODE"; payload: TreeNodeItem | null }
    | { type: "TOGGLE_NODE_EXPANDED"; payload: string }
    | {
          type: "ADD_ASSET_LINK";
          payload: { relationshipType: "related" | "parent" | "child"; asset: AssetNode };
      }
    | {
          type: "REMOVE_ASSET_LINK";
          payload: { relationshipType: "related" | "parent" | "child"; assetId: string };
      }
    | { type: "SET_INITIAL_DATA"; payload: NewAssetLinksData }
    | { type: "MARK_CHANGES_PROCESSED"; payload: null }
    | {
          type: "ADD_ASSET_LINK_METADATA";
          payload: {
              assetId: string;
              relationshipType: "related" | "parent" | "child";
              metadata: AssetLinkMetadata;
          };
      }
    | {
          type: "UPDATE_ASSET_LINK_METADATA";
          payload: {
              assetId: string;
              relationshipType: "related" | "parent" | "child";
              metadata: AssetLinkMetadata;
          };
      }
    | {
          type: "DELETE_ASSET_LINK_METADATA";
          payload: {
              assetId: string;
              relationshipType: "related" | "parent" | "child";
              metadataKey: string;
          };
      };

export interface CreateAssetLinkModalProps {
    visible: boolean;
    onDismiss: () => void;
    relationshipType: "related" | "parent" | "child";
    currentAssetId: string;
    currentDatabaseId: string;
    onSuccess: (assetNode?: AssetNode, relationshipType?: "related" | "parent" | "child") => void;
    noOpenSearch?: boolean;
    isSubChildMode?: boolean;
    parentAssetData?: any;
}

export interface DeleteAssetLinkModalProps {
    visible: boolean;
    onDismiss: () => void;
    assetLinkId: string;
    assetName: string;
    relationshipType: "related" | "parent" | "child";
    onSuccess: () => void;
    isSubChild?: boolean;
    parentAssetName?: string;
}
