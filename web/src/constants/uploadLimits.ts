/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Upload limits and constants for file upload operations.
 * The backend's per-request caps are MAX_FILES_PER_UPLOAD_REQUEST, MAX_PARTS_PER_FILE and
 * MAX_TOTAL_PARTS_PER_UPLOAD_REQUEST in backend/backend/models/assetsV3.py; the values below are
 * this app's own batch sizes and stay within those caps.
 */

// Maximum number of concurrent uploads
export const MAX_CONCURRENT_UPLOADS = 6;

// Part size configuration
export const MAX_PART_SIZE = 150 * 1024 * 1024; // 150MB
export const MAX_PART_SIZE_LARGE = 1024 * 1024 * 1024; // 1GB for very large files
export const MAX_FILE_SIZE_SMALL_CHUNKS = 15 * 1024 * 1024 * 1024; // 15GB threshold

// Client batch sizes. MAX_TOTAL_PARTS_PER_REQUEST also decides when an upload is split into
// sequences (uploadSequencer.ts needsMultiSequenceUpload) and drives the multi-sequence display.
// MAX_PARTS_PER_FILE is read only by validateUploadConstraints.
export const MAX_FILES_PER_REQUEST = 50; // Files per upload init/complete call
export const MAX_TOTAL_PARTS_PER_REQUEST = 200; // Total parts across all files per call
export const MAX_PARTS_PER_FILE = 200; // Parts per individual file

// Sequence grouping
export const MAX_SEQUENCE_SIZE = 3 * 1024 * 1024 * 1024; // 3GB soft limit for grouping files

// Retry configuration
export const MAX_RETRY_ATTEMPTS = 3;

// Preview file limits
export const MAX_PREVIEW_FILE_SIZE = 5 * 1024 * 1024; // 5MB

// Allowed preview extensions are defined once in common/constants/fileFormats.ts
export { previewFileFormats as ALLOWED_PREVIEW_EXTENSIONS } from "../common/constants/fileFormats";
