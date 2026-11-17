/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// File formats used for upload preview validation and file preview thumbnails.
// All other (system) file types are defined by the visualizer plugin system
// configuration (viewerConfig.json) and should not be maintained here.
// Mirrored in the backend as ALLOWED_PREVIEW_FILE_EXTENSIONS in
// backend/backend/common/s3PathPatterns.py; keep the two in sync.
export const previewFileFormats = [".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".bmp"];

// The size limit of a preview file (5 MiB). A file is shown as its own preview only when its size is
// STRICTLY UNDER this limit, and the media preview pipeline writes only previews strictly under it.
// Mirrored in the backend as PREVIEW_FILE_SIZE_LIMIT_BYTES in
// backend/backend/common/s3PathPatterns.py and in the 3D thumbnail container as
// image_utils.MAX_FILE_SIZE_BYTES; drift tests keep the three equal.
export const PREVIEW_FILE_SIZE_LIMIT_BYTES = 5 * 1024 * 1024;

/**
 * True when the web shows the file itself as its preview: its extension is in previewFileFormats and
 * its size is known and strictly under PREVIEW_FILE_SIZE_LIMIT_BYTES. TGA and TIFF are not in the
 * list, so they never count: browsers cannot display them. Mirrors is_self_previewable in
 * backend/backend/common/s3PathPatterns.py.
 */
export const isSelfPreviewable = (fileName: string, size: number | undefined | null): boolean => {
    if (typeof size !== "number" || !Number.isFinite(size)) return false;
    const dot = fileName.lastIndexOf(".");
    if (dot < 0) return false;
    return (
        previewFileFormats.includes(fileName.substring(dot).toLowerCase()) &&
        size < PREVIEW_FILE_SIZE_LIMIT_BYTES
    );
};

// Marker substring identifying a file-level preview file. Preview files are
// stored next to their base file as {baseFile}.previewFile.{ext}
// (e.g. model.gltf.previewFile.png); a file name is a preview file iff it
// CONTAINS this substring. Mirrored in the backend as PREVIEW_FILE_PATTERN in
// backend/backend/common/s3PathPatterns.py; keep the two in sync.
export const PREVIEW_FILE_PATTERN = ".previewFile.";

// Formats whose files can reference other files by relative path (USD sublayers, glTF buffers and
// textures, OBJ materials, CAD assemblies, 3D Tiles). When one is added as a loose file the upload
// control suggests Add folder, so the referenced files keep their relative paths (#1462).
export const referencingFileExtensions = [
    ".usd",
    ".usda",
    ".usdc",
    ".usdz",
    ".gltf",
    ".obj",
    ".fbx",
    ".dae",
    ".step",
    ".stp",
    ".ifc",
    ".iges",
    ".igs",
    ".sldasm",
    ".catproduct",
    ".asm",
];

// Exact (lower-case) file names that reference other files.
export const referencingFileNames = ["tileset.json"];
