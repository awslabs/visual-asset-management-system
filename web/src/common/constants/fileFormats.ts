/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// File formats used for upload preview validation and file preview thumbnails.
// All other (system) file types are defined by the visualizer plugin system
// configuration (viewerConfig.json) and should not be maintained here.
// Mirrored in the backend as ALLOWED_PREVIEW_FILE_EXTENSIONS in
// backend/backend/common/s3PathPatterns.py; keep the two in sync.
export const previewFileFormats = [".png", ".jpg", ".jpeg", ".svg", ".gif"];

// Marker substring identifying a file-level preview file. Preview files are
// stored next to their base file as {baseFile}.previewFile.{ext}
// (e.g. model.gltf.previewFile.png); a file name is a preview file iff it
// CONTAINS this substring. Mirrored in the backend as PREVIEW_FILE_PATTERN in
// backend/backend/common/s3PathPatterns.py; keep the two in sync.
export const PREVIEW_FILE_PATTERN = ".previewFile.";
