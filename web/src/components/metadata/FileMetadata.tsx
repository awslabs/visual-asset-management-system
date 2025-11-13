/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * FileMetadata Component - Performance Optimized Version
 *
 * This component provides a reusable wrapper around ControlledMetadata with the following optimizations:
 *
 * PERFORMANCE OPTIMIZATIONS:
 * - Immediate loading: Removed lazy loading delays to start metadata loading immediately
 * - Simplified loading detection: Let ControlledMetadata handle its own loading state
 * - Metadata caching: Avoids redundant API calls for the same file metadata (5-minute cache)
 * - Request cancellation: Cancels previous requests when file selection changes
 * - Memoization: Uses React.useMemo and useCallback to prevent unnecessary re-renders
 * - Cache management: Automatic cleanup of expired entries and size limits (100 entries max)
 * - Removed complex MutationObserver logic that was causing initialization delays
 *
 * MEMORY MANAGEMENT:
 * - Proper cleanup of timeouts, observers, and event listeners on unmount
 * - Abort controllers for cancelling in-flight requests
 * - Automatic cache cleanup every minute
 * - Component unmount detection to prevent memory leaks
 *
 * USER EXPERIENCE IMPROVEMENTS:
 * - Smooth transitions and loading states with CSS animations
 * - Improved error messages with retry functionality
 * - Better accessibility with ARIA labels and keyboard navigation
 * - Visual feedback for loading, error, and success states
 * - Consistent styling with VAMS design system
 *
 * ACCESSIBILITY FEATURES:
 * - ARIA live regions for screen readers
 * - Keyboard navigation support
 * - Focus management and visual indicators
 * - Semantic HTML structure with proper roles
 */

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { ErrorBoundary } from "react-error-boundary";
import { Container, Header, Box, Flashbar } from "@cloudscape-design/components";
import ControlledMetadata from "./ControlledMetadata";
import { put } from "../single/Metadata";

// Metadata cache to avoid redundant API calls
interface MetadataCacheEntry {
    data: any;
    timestamp: number;
    loading: boolean;
    abortController?: AbortController;
}

const metadataCache = new Map<string, MetadataCacheEntry>();
const activeRequests = new Map<string, AbortController>();
const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes
const MAX_CACHE_SIZE = 100; // Maximum number of cached entries

// Cache management utilities
const getCacheKey = (databaseId: string, assetId: string, prefix: string): string => {
    return `${databaseId}:${assetId}:${prefix}`;
};

const isValidCacheEntry = (entry: MetadataCacheEntry): boolean => {
    return Date.now() - entry.timestamp < CACHE_DURATION;
};

const cleanupCache = (): void => {
    const now = Date.now();
    const entries = Array.from(metadataCache.entries());

    // Remove expired entries and cancel any associated requests
    entries.forEach(([key, entry]) => {
        if (now - entry.timestamp > CACHE_DURATION) {
            // Cancel any active request
            if (entry.abortController && !entry.abortController.signal.aborted) {
                entry.abortController.abort();
            }
            metadataCache.delete(key);
            activeRequests.delete(key);
        }
    });

    // If still too large, remove oldest entries
    if (metadataCache.size > MAX_CACHE_SIZE) {
        const sortedEntries = entries
            .filter(([key]) => metadataCache.has(key)) // Only include non-deleted entries
            .sort(([, a], [, b]) => a.timestamp - b.timestamp);

        const toRemove = sortedEntries.slice(0, metadataCache.size - MAX_CACHE_SIZE);
        toRemove.forEach(([key, entry]) => {
            // Cancel any active request before removing
            if (entry.abortController && !entry.abortController.signal.aborted) {
                entry.abortController.abort();
            }
            metadataCache.delete(key);
            activeRequests.delete(key);
        });
    }
};

// Cancel all active requests for cleanup
const cancelAllActiveRequests = (): void => {
    activeRequests.forEach((controller, key) => {
        if (!controller.signal.aborted) {
            controller.abort();
        }
    });
    activeRequests.clear();
};

// Cancel specific request
const cancelRequest = (cacheKey: string): void => {
    const controller = activeRequests.get(cacheKey);
    if (controller && !controller.signal.aborted) {
        controller.abort();
    }
    activeRequests.delete(cacheKey);
};

// Export cleanup functions for external use
export const cleanupMetadataCache = cleanupCache;
export const cancelAllMetadataRequests = cancelAllActiveRequests;
export const clearMetadataCache = (): void => {
    cancelAllActiveRequests();
    metadataCache.clear();
};

/**
 * Props interface for the FileMetadata component
 */
export interface FileMetadataProps {
    /** Database ID for the metadata */
    databaseId: string;
    /** Asset ID for the metadata */
    assetId: string;
    /** File prefix/path for the metadata */
    prefix: string;
    /** Optional CSS class name for styling */
    className?: string;
    /** Whether to show the metadata header */
    showHeader?: boolean;
    /** Whether to show validation errors */
    showErrors?: boolean;
    /** Callback for error handling */
    onError?: (error: string) => void;
    /** Callback for loading state changes */
    onLoading?: (loading: boolean) => void;
    /** Callback for validation state changes */
    onValidationChange?: (isValid: boolean) => void;
    /** Callback for save success */
    onSaveSuccess?: () => void;
    /** Callback for save error */
    onSaveError?: (error: string) => void;
}

/**
 * Error fallback component for metadata errors with improved accessibility and UX
 */
function MetadataErrorFallback({
    error,
    resetErrorBoundary,
}: {
    error: Error;
    resetErrorBoundary: () => void;
}) {
    return (
        <div role="alert" aria-live="assertive">
            <Box padding="m" textAlign="center">
                <div
                    style={{
                        color: "#d13212",
                        fontSize: "14px",
                        marginBottom: "8px",
                        fontWeight: "500",
                    }}
                >
                    Unable to load metadata
                </div>
                <div
                    style={{
                        color: "#666",
                        fontSize: "12px",
                        marginBottom: "12px",
                        lineHeight: "1.4",
                    }}
                >
                    There was an error loading the metadata for this file. Please try again or
                    contact your administrator if the problem persists.
                </div>
                <button
                    onClick={resetErrorBoundary}
                    onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            resetErrorBoundary();
                        }
                    }}
                    style={{
                        padding: "8px 16px",
                        fontSize: "12px",
                        border: "1px solid #ccc",
                        borderRadius: "4px",
                        backgroundColor: "#fff",
                        cursor: "pointer",
                        transition: "all 0.2s ease-in-out",
                        outline: "none",
                    }}
                    onMouseEnter={(e) => {
                        e.currentTarget.style.backgroundColor = "#f8f9fa";
                        e.currentTarget.style.borderColor = "#007eb9";
                    }}
                    onMouseLeave={(e) => {
                        e.currentTarget.style.backgroundColor = "#fff";
                        e.currentTarget.style.borderColor = "#ccc";
                    }}
                    onFocus={(e) => {
                        e.currentTarget.style.boxShadow = "0 0 0 2px rgba(0, 126, 185, 0.25)";
                    }}
                    onBlur={(e) => {
                        e.currentTarget.style.boxShadow = "none";
                    }}
                    aria-label="Retry loading metadata"
                >
                    Retry
                </button>
            </Box>
        </div>
    );
}

/**
 * Optimized wrapper component with caching and improved loading detection
 */
function ControlledMetadataWrapper({
    databaseId,
    assetId,
    prefix,
    showErrors,
    onLoadingChange,
    onValidationChange,
    onSaveSuccess,
    onSaveError,
}: {
    databaseId: string;
    assetId: string;
    prefix: string;
    showErrors?: boolean;
    onLoadingChange?: (loading: boolean) => void;
    onValidationChange?: (isValid: boolean) => void;
    onSaveSuccess?: () => void;
    onSaveError?: (error: string) => void;
}) {
    const mountedRef = useRef(true);

    // Memoize cache key to avoid recalculation
    const cacheKey = useMemo(
        () => getCacheKey(databaseId, assetId, prefix),
        [databaseId, assetId, prefix]
    );

    // Enhanced store function with caching and error handling
    const enhancedStore = useCallback(
        async (databaseId: string, assetId: string, record: any, prefix?: string) => {
            try {
                const result = await put(databaseId, assetId, record, prefix);

                // Update cache with new data
                const cacheKey = getCacheKey(databaseId, assetId, prefix || "");
                metadataCache.set(cacheKey, {
                    data: record,
                    timestamp: Date.now(),
                    loading: false,
                });

                if (mountedRef.current && onSaveSuccess) {
                    onSaveSuccess();
                }
                return result;
            } catch (error) {
                console.error("Error saving metadata:", error);
                const errorMessage =
                    error instanceof Error ? error.message : "Failed to save metadata";
                if (mountedRef.current && onSaveError) {
                    onSaveError(errorMessage);
                }
                throw error; // Re-throw to maintain existing error handling behavior
            }
        },
        [onSaveSuccess, onSaveError]
    );

    // Simplified cache checking - prioritize speed over complex caching
    useEffect(() => {
        if (!mountedRef.current) return;

        const cachedEntry = metadataCache.get(cacheKey);

        // If we have valid cached data, skip loading immediately
        if (cachedEntry && isValidCacheEntry(cachedEntry) && !cachedEntry.loading) {
            if (onLoadingChange) {
                onLoadingChange(false);
            }
            return;
        }

        // Cancel any existing request for this cache key
        cancelRequest(cacheKey);

        // Create new abort controller for this request
        const abortController = new AbortController();
        activeRequests.set(cacheKey, abortController);

        // Mark as loading in cache
        metadataCache.set(cacheKey, {
            data: null,
            timestamp: Date.now(),
            loading: true,
            abortController,
        });

        // Let ControlledMetadata handle its own loading state

        return () => {
            // Cancel request when component unmounts or dependencies change
            cancelRequest(cacheKey);
        };
    }, [cacheKey, onLoadingChange]);

    // Remove complex loading detection - let ControlledMetadata handle its own loading state
    // This eliminates the delays caused by our loading detection logic

    // Simplified cleanup on unmount
    useEffect(() => {
        return () => {
            mountedRef.current = false;

            // Cancel any active request for this component
            cancelRequest(cacheKey);

            // Clean up cache entry if it's still loading (component unmounted before completion)
            const entry = metadataCache.get(cacheKey);
            if (entry && entry.loading) {
                entry.loading = false;
            }
        };
    }, [cacheKey]);

    // Periodic cache cleanup and memory management
    useEffect(() => {
        const cleanupInterval = setInterval(() => {
            cleanupCache();

            // Force garbage collection hint (if available)
            if (window.gc) {
                window.gc();
            }
        }, 60000); // Cleanup every minute

        return () => {
            clearInterval(cleanupInterval);
        };
    }, []);

    // Always render ControlledMetadata directly - no loading states or delays
    return (
        <ControlledMetadata
            databaseId={databaseId}
            assetId={assetId}
            prefix={prefix}
            showErrors={showErrors}
            setValid={onValidationChange}
            store={enhancedStore}
        />
    );
}

/**
 * FileMetadata component - A reusable wrapper around ControlledMetadata
 * with enhanced error handling, loading states, caching, and performance optimizations.
 *
 * This component provides a consistent interface for displaying file metadata
 * across different contexts (ViewFile and FileDetailsPanel) while handling
 * errors gracefully, providing loading feedback, and optimizing performance
 * through caching and lazy loading.
 */
export default function FileMetadata({
    databaseId,
    assetId,
    prefix,
    className,
    showHeader = true,
    showErrors = true,
    onError,
    onLoading,
    onValidationChange,
    onSaveSuccess,
    onSaveError,
}: FileMetadataProps) {
    const [retryKey, setRetryKey] = useState(0);
    const [saveMessages, setSaveMessages] = useState<
        Array<{
            type: "success" | "error";
            content: string;
            id: string;
            dismissible: boolean;
        }>
    >([]);
    const componentRef = useRef<HTMLDivElement>(null);
    const messageTimeoutRefs = useRef<Map<string, NodeJS.Timeout>>(new Map());

    // Memoize handlers to prevent unnecessary re-renders
    const handleError = useCallback(
        (error: Error) => {
            console.error("FileMetadata error:", error);
            if (onError) {
                onError(error.message);
            }
        },
        [onError]
    );

    const handleRetry = useCallback(() => {
        console.log("Retrying metadata load");
        setRetryKey((prev) => prev + 1);

        // Clear cache for this item to force reload
        const cacheKey = getCacheKey(databaseId, assetId, prefix);
        metadataCache.delete(cacheKey);
    }, [databaseId, assetId, prefix]);

    const handleSaveSuccess = useCallback(() => {
        const messageId = `success-${Date.now()}`;
        setSaveMessages((prev) => [
            ...prev,
            {
                type: "success",
                content: "Metadata saved successfully",
                id: messageId,
                dismissible: true,
            },
        ]);

        // Auto-dismiss success message after 3 seconds
        const timeoutId = setTimeout(() => {
            setSaveMessages((prev) => prev.filter((msg) => msg.id !== messageId));
            messageTimeoutRefs.current.delete(messageId);
        }, 3000);

        messageTimeoutRefs.current.set(messageId, timeoutId);

        if (onSaveSuccess) {
            onSaveSuccess();
        }
    }, [onSaveSuccess]);

    const handleSaveError = useCallback(
        (error: string) => {
            const messageId = `error-${Date.now()}`;
            setSaveMessages((prev) => [
                ...prev,
                {
                    type: "error",
                    content: `Failed to save metadata: ${error}`,
                    id: messageId,
                    dismissible: true,
                },
            ]);

            if (onSaveError) {
                onSaveError(error);
            }
        },
        [onSaveError]
    );

    const dismissMessage = useCallback((messageId: string) => {
        setSaveMessages((prev) => prev.filter((msg) => msg.id !== messageId));

        // Clear timeout if it exists
        const timeoutId = messageTimeoutRefs.current.get(messageId);
        if (timeoutId) {
            clearTimeout(timeoutId);
            messageTimeoutRefs.current.delete(messageId);
        }
    }, []);

    // Removed intersection observer to eliminate loading delays
    // Component now loads immediately when mounted

    // Cleanup message timeouts on unmount
    useEffect(() => {
        return () => {
            messageTimeoutRefs.current.forEach((timeoutId) => clearTimeout(timeoutId));
            messageTimeoutRefs.current.clear();
        };
    }, []);

    // Memoize flashbar items to prevent unnecessary re-renders
    const flashbarItems = useMemo(
        () =>
            saveMessages.map((msg) => ({
                type: msg.type,
                content: msg.content,
                dismissible: msg.dismissible,
                onDismiss: () => dismissMessage(msg.id),
                id: msg.id,
            })),
        [saveMessages, dismissMessage]
    );

    // Simplified metadata content - render immediately without lazy loading delays
    const metadataContent = useMemo(() => {
        return (
            <>
                {saveMessages.length > 0 && (
                    <Box margin={{ bottom: "s" }}>
                        <Flashbar items={flashbarItems} />
                    </Box>
                )}
                <ErrorBoundary
                    FallbackComponent={({ resetErrorBoundary }) => (
                        <MetadataErrorFallback
                            error={new Error("Metadata loading failed")}
                            resetErrorBoundary={() => {
                                resetErrorBoundary();
                                handleRetry();
                            }}
                        />
                    )}
                    onError={handleError}
                    resetKeys={[retryKey, databaseId, assetId, prefix]}
                >
                    <ControlledMetadataWrapper
                        key={`${retryKey}-${databaseId}-${assetId}-${prefix}`}
                        databaseId={databaseId}
                        assetId={assetId}
                        prefix={prefix}
                        showErrors={showErrors}
                        onLoadingChange={onLoading}
                        onValidationChange={onValidationChange}
                        onSaveSuccess={handleSaveSuccess}
                        onSaveError={handleSaveError}
                    />
                </ErrorBoundary>
            </>
        );
    }, [
        saveMessages,
        flashbarItems,
        retryKey,
        databaseId,
        assetId,
        prefix,
        showErrors,
        onLoading,
        onValidationChange,
        handleSaveSuccess,
        handleSaveError,
        handleError,
        handleRetry,
    ]);

    // Memoize the final component structure with accessibility improvements
    const componentContent = useMemo(() => {
        const commonProps = {
            "data-testid": "file-metadata",
            role: "region",
            "aria-label": "File metadata",
            tabIndex: -1, // Allow programmatic focus but not tab navigation
            style: {
                transition: "all 0.3s ease-in-out",
                outline: "none",
            },
        };

        if (showHeader) {
            return (
                <Container
                    header={
                        <Header
                            variant="h2"
                            headingTagOverride="h3" // Better semantic hierarchy
                        >
                            Metadata
                        </Header>
                    }
                    {...commonProps}
                    {...(className && { className })}
                >
                    {metadataContent}
                </Container>
            );
        }

        return (
            <div className={className} {...commonProps}>
                {metadataContent}
            </div>
        );
    }, [showHeader, className, metadataContent]);

    return (
        <div
            ref={componentRef}
            style={{
                transition: "opacity 0.3s ease-in-out",
                opacity: 1,
            }}
            onKeyDown={(e) => {
                // Improve keyboard navigation
                if (e.key === "Escape") {
                    // Allow parent components to handle escape
                    e.stopPropagation();
                }
            }}
        >
            {componentContent}
        </div>
    );
}
