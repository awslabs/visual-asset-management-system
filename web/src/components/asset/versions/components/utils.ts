/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Normalize path to ensure consistent format
 * @param path Path to normalize
 * @returns Normalized path without leading slash
 */
export const normalizePath = (path: string): string => {
    // Remove leading slash if present
    return path.startsWith('/') ? path.substring(1) : path;
};

/**
 * Format file size for display
 * @param size File size in bytes
 * @returns Formatted file size string
 */
export const formatFileSize = (size?: number): string => {
    if (size === undefined) return 'Unknown';
    if (size === 0) return '0 B';
    
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(size) / Math.log(1024));
    return `${(size / Math.pow(1024, i)).toFixed(2)} ${units[i]}`;
};

/**
 * Format date for display
 * @param dateString Date string
 * @returns Formatted date string
 */
export const formatDate = (dateString?: string): string => {
    if (!dateString) return 'Unknown';
    try {
        const date = new Date(dateString);
        return date.toLocaleString();
    } catch (e) {
        return dateString;
    }
};
