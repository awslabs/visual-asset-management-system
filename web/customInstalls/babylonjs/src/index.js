// Import BabylonJS core
import * as BABYLON from "@babylonjs/core";

// Import loaders (SPLATFileLoader default flipY changed to true in splatFileLoader.js)
import "@babylonjs/loaders";

// Import Gaussian Splatting mesh support
import "@babylonjs/core/Meshes/GaussianSplatting/gaussianSplattingMesh";

// Import fflate for .sog/.spz file decompression
import * as fflate from "fflate";

// Make fflate available globally for BabylonJS loaders
if (typeof window !== "undefined") {
    window.fflate = fflate;
}

// Export BABYLON as the default export for UMD
export default BABYLON;

// Also export all named exports from BABYLON
export * from "@babylonjs/core";
