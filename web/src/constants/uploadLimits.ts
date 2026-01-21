/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Upload limits and constants for file upload operations.
 * These limits match the backend API constraints and CLI implementation.
 */

// Maximum number of concurrent uploads
export const MAX_CONCURRENT_UPLOADS = 6;

// Part size configuration
export const MAX_PART_SIZE = 150 * 1024 * 1024; // 150MB
export const MAX_PART_SIZE_LARGE = 1024 * 1024 * 1024; // 1GB for very large files
export const MAX_FILE_SIZE_SMALL_CHUNKS = 15 * 1024 * 1024 * 1024; // 15GB threshold

// Backend upload limits (v2.2+)
export const MAX_FILES_PER_REQUEST = 50; // Maximum files per upload init/complete call
export const MAX_TOTAL_PARTS_PER_REQUEST = 200; // Maximum total parts across all files per request
export const MAX_PARTS_PER_FILE = 200; // Maximum parts per individual file

// Sequence grouping
export const MAX_SEQUENCE_SIZE = 3 * 1024 * 1024 * 1024; // 3GB soft limit for grouping files

// Retry configuration
export const MAX_RETRY_ATTEMPTS = 3;

// Preview file limits
export const MAX_PREVIEW_FILE_SIZE = 5 * 1024 * 1024; // 5MB
export const ALLOWED_PREVIEW_EXTENSIONS = [".png", ".jpg", ".jpeg", ".svg", ".gif"];
