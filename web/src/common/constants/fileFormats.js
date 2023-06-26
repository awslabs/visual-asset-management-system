/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export const pcFileFormats = [".e57", ".las", ".laz"];
export const cadFileFormats = [".step", ".dwg", ".sldasm", ".stp", ".fcstd", "3dm", "brep", ".ifc"];
export const modelFileFormats = [
    ".obj",
    ".gltf",
    ".glb",
    ".stl",
    ".3ds",
    ".ply",
    ".fbx",
    ".dae",
    ".wrl",
    ".3mf",
    ".off",
    ".bim",
];
export const columnarFileFormats = [".rds", ".fcs", ".csv"];
export const previewFileFormats = [".png", ".jpg", ".svg", ".gif"];
export const archiveFileFormats = [".zip"];
//html files are view-only, should not be made available for upload or as sources in pipeline
//there may be a need in some cases to output notes on the pipeline execution
//future state could add txt and rename from "presentation"
export const presentationFileFormats = [".html"];

export const FILE_FORMATS = {
    CAD: cadFileFormats,
    PRINT: [],
    VR: [],
    PC: pcFileFormats,
    MODEL: modelFileFormats,
    COLUMNAR: columnarFileFormats,
    PREVIEW: previewFileFormats,
    ARCHIVE: archiveFileFormats,
    PRESENTATION: presentationFileFormats,
};
