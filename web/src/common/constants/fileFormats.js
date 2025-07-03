/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export const audioFileFormats = [".mp3", ".wav", ".ogg", ".aac", ".flac", ".m4a"];
export const videoFileFormats = [".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v"];
export const pcFileFormats = [".e57", ".las", ".laz", ".ply"];
export const cadFileFormats = [
    ".step",
    ".dwg",
    ".sldasm",
    ".stp", //step
    ".step", //step
    ".fcstd",
    ".3dm",
    ".brep",
    ".ifc",
    ".iges",
];
export const modelFileFormats = [
    ".obj",
    ".gltf",
    ".glb",
    ".stl",
    ".3ds",
    ".fbx",
    ".dae",
    ".wrl",
    ".3mf",
    ".off",
    ".bim",
    ".ifc",
    ".amf",
    ".usdz",
    ".usd",
];
export const columnarFileFormats = [".rds", ".fcs", ".csv"];
export const imageFileFormats = [".png", ".jpg", ".jpeg", ".svg", ".gif"];
export const previewFileFormats = imageFileFormats;
export const archiveFileFormats = [".zip"];
//html files are view-only, should not be made available for upload or as sources in pipeline
//there may be a need in some cases to output notes on the pipeline execution
//future state could add txt and rename from "presentation"
export const presentationFileFormats = [".html"];

export const onlineViewer3DFileFormats = [
    ".3dm",
    ".3ds",
    ".3mf",
    ".amf",
    ".bim",
    ".dae",
    ".fbx",
    ".gltf",
    ".glb",
    ".stl",
    ".obj",
    ".off",
    //".ply" //- Excluded as it will be shown on the point clouder viewer instead
    ".wrl",
    //".fcstd", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
    //".ifc", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
    //".iges", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
    //".step", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
    //".stp", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
    //".brep", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
];

export const FILE_FORMATS = {
    CAD: cadFileFormats,
    PRINT: [],
    VR: [],
    PC: pcFileFormats,
    IMAGE: imageFileFormats,
    MODEL: modelFileFormats,
    ONLINE_3D_VIEWER: onlineViewer3DFileFormats,
    COLUMNAR: columnarFileFormats,
    PREVIEW: previewFileFormats,
    ARCHIVE: archiveFileFormats,
    PRESENTATION: presentationFileFormats,
    VIDEO: videoFileFormats,
    AUDIO: audioFileFormats,
};
