/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// File formats used for asset filtering and pipeline configuration
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
export const archiveFileFormats = [".zip"];

// Still used by upload components and file preview functionality
export const previewFileFormats = imageFileFormats;

// Still used by AssetSelector for filtering
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
    //".ply" //- Excluded as it will be shown on the point cloud viewer instead
    ".wrl",
    //".fcstd", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
    //".ifc", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
    //".iges", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
    //".step", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
    //".stp", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
    //".brep", //- Excluded by default due to license restrictive sub-library use. Enable if you accept this license. Install the appropriate library listed in documentation.
];

// Note: Audio, video, presentation, and online3D viewer file formats are now
// defined in the visualizer plugin system configuration (viewerConfig.json)
// and no longer need to be maintained here.
