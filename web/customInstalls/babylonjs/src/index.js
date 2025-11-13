// Import BabylonJS core
import * as BABYLON from "@babylonjs/core";

// Import loaders
import "@babylonjs/loaders";

// Import Gaussian Splatting mesh support
import "@babylonjs/core/Meshes/GaussianSplatting/gaussianSplattingMesh";

// Export BABYLON as the default export for UMD
export default BABYLON;

// Also export all named exports from BABYLON
export * from "@babylonjs/core";
