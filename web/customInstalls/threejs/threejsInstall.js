/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Import THREE.js core
import * as THREE from "three";

// Import loaders
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { FBXLoader } from "three/examples/jsm/loaders/FBXLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { ColladaLoader } from "three/examples/jsm/loaders/ColladaLoader.js";
import { TDSLoader } from "three/examples/jsm/loaders/TDSLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";

// Import controls
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { TransformControls } from "three/examples/jsm/controls/TransformControls.js";

// Optional: Import OCCT if available (will be undefined if not installed)
let occtimportjs;
try {
    occtimportjs = require("occt-import-js");
} catch (e) {
    // OCCT not installed - this is expected by default
    console.log("OCCT-import-js not available (optional dependency)");
}

// Export everything as a bundle
export default {
    THREE,
    GLTFLoader,
    OBJLoader,
    FBXLoader,
    STLLoader,
    PLYLoader,
    ColladaLoader,
    TDSLoader,
    ThreeMFLoader,
    OrbitControls,
    TransformControls,
    occtimportjs, // Will be undefined if not installed
};
