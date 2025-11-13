/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Main component
export { default as ModernSearchContainer } from "./ModernSearchContainer";

// Types
export * from "./types";

// Hooks
export { useSearchState } from "./hooks/useSearchState";
export { useSearchAPI } from "./hooks/useSearchAPI";
export { usePreferences } from "./hooks/usePreferences";
export { useToasts } from "./hooks/useToasts";

// Filter components
export { default as BasicFilters } from "./SearchFilters/BasicFilters";
export { default as MetadataFilters } from "./SearchFilters/MetadataFilters";

// Result components
export { default as CardView } from "./SearchResults/CardView";

// Notification components
export { default as ToastManager } from "./SearchNotifications/ToastManager";
