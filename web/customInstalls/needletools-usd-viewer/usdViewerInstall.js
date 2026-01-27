/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

//npm install simple-git fs-extra
const { simpleGit, SimpleGit, SimpleGitOptions } = require("simple-git");
const { execSync } = require("child_process");
const fs = require("fs-extra");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");

// Configurations
const viewerId = "needletools-usd-viewer";
const gitRepoSourceDestDir = "./customInstalls/needletools-usd-viewer/source"; //Relative to base web directory where yarn/npm is run
const gitRepoUrl = "https://github.com/needle-tools/usd-viewer.git";
const gitRepoCommitHash = "aab170a";

const destinationDir = "./public/viewers/needletools_usd_viewer"; //Relative to base web directory where yarn/npm is run

// Function to cleanup previous git source and build binaries
const previousCleanUp = async () => {
    try {
        await fs.rmSync(gitRepoSourceDestDir, { recursive: true, force: true });
        await fs.rmSync(destinationDir, { recursive: true, force: true });
        console.log("NeedleTools-USDViewer Previous Build Cleanup Complete");
    } catch (err) {
        console.error("NeedleTools-USDViewer Previous Build Cleanup error:", err);
    }
};

// Function to perform Git pull, checkout to specific commit, and apply patch
const gitActions = async () => {
    try {
        await simpleGit().clone(gitRepoUrl, gitRepoSourceDestDir);
        console.log("NeedleTools-USDViewer Build Git Clone complete");

        const git = simpleGit({ baseDir: gitRepoSourceDestDir });
        await git.checkout(gitRepoCommitHash);
        console.log("NeedleTools-USDViewer Build Git Checkout Specific Commit complete");
        console.log("NeedleTools-USDViewer Build Git complete");
    } catch (err) {
        console.error("NeedleTools-USDViewer Build Git error:", err);
    }
};

// Function to run NPM install on NeedleTools-USDViewer Source
// Note: The USD viewer repo includes pre-built WASM files, so we don't need to build
const npmBuild = () => {
    try {
        execSync("npm install", { cwd: gitRepoSourceDestDir }); //Install dependencies
        console.log("NeedleTools-USDViewer Build NPM install complete");
        // Note: npm run build is not needed as the repo includes pre-built files
        console.log("NeedleTools-USDViewer: Using pre-built WASM files from repository");
    } catch (err) {
        console.error("NeedleTools-USDViewer Build NPM install error:", err);
    }
};

// Function to bundle USD viewer with webpack
const bundleWithWebpack = async () => {
    try {
        const bundleDir = gitRepoSourceDestDir + "/bundle_build";
        await fs.mkdir(bundleDir, { recursive: true });

        // Create package.json with webpack and three.js
        const packageJson = {
            name: "usd-viewer-bundle",
            version: "1.0.0",
            dependencies: {
                three: "^0.160.0",
            },
            devDependencies: {
                webpack: "^5.89.0",
                "webpack-cli": "^5.1.4",
            },
        };
        await fs.writeJson(bundleDir + "/package.json", packageJson);

        // Install dependencies
        console.log("NeedleTools-USDViewer: Installing webpack and Three.js...");
        execSync("npm install", { cwd: bundleDir, stdio: "inherit" });

        // Copy ThreeJsRenderDelegate to bundle directory for easier import
        const renderDelegateSource =
            gitRepoSourceDestDir + "/usd-wasm/src/hydra/ThreeJsRenderDelegate.js";
        await fs.copy(renderDelegateSource, bundleDir + "/ThreeJsRenderDelegate.js");

        // Create webpack entry point that imports and exposes everything
        const entryPointContent = `
import * as THREE from 'three';
import { ThreeRenderDelegateInterface } from './ThreeJsRenderDelegate.js';

// Expose THREE globally
window.THREE = THREE;

// Expose ThreeRenderDelegateInterface globally
window.ThreeRenderDelegateInterface = ThreeRenderDelegateInterface;

console.log('USD Viewer bundle loaded successfully');
`;
        await fs.writeFile(bundleDir + "/entry.js", entryPointContent);

        // Create webpack config
        const webpackConfig = `
const path = require('path');

module.exports = {
  mode: 'production',
  entry: './entry.js',
  output: {
    filename: 'usd-viewer-bundle.js',
    path: path.resolve(__dirname, 'dist'),
    library: {
      name: 'USDViewer',
      type: 'window',
    },
  },
  resolve: {
    extensions: ['.js'],
  },
  performance: {
    maxAssetSize: 5000000,
    maxEntrypointSize: 5000000,
  },
};
`;
        await fs.writeFile(bundleDir + "/webpack.config.js", webpackConfig);

        // Run webpack
        console.log("NeedleTools-USDViewer: Running webpack to bundle USD viewer...");
        execSync("npx webpack --config webpack.config.js", {
            cwd: bundleDir,
            stdio: "inherit",
        });

        // Copy bundled file to destination
        const bundledFile = bundleDir + "/dist/usd-viewer-bundle.js";
        if (await fs.pathExists(bundledFile)) {
            await fs.copy(bundledFile, destinationDir + "/usd-viewer-bundle.js");
            console.log("NeedleTools-USDViewer: Copied bundled USD viewer");
        } else {
            console.error("NeedleTools-USDViewer: Bundled file not found!");
        }

        // Clean up bundle directory
        await fs.rmSync(bundleDir, { recursive: true, force: true });
        console.log("NeedleTools-USDViewer: Webpack bundling complete");
    } catch (err) {
        console.error("NeedleTools-USDViewer Webpack bundling error:", err);
    }
};

// Function to copy files to destination directory
const copyFiles = async () => {
    try {
        // Create destination directory
        await fs.mkdir(destinationDir, { recursive: true });
        console.log("NeedleTools-USDViewer: Created destination directory");

        // Copy USD WASM bindings from usd-wasm/src/bindings directory
        const wasmBindingsDir = gitRepoSourceDestDir + "/usd-wasm/src/bindings";

        // Copy WASM files
        await fs.copy(wasmBindingsDir + "/emHdBindings.js", destinationDir + "/emHdBindings.js");
        await fs.copy(
            wasmBindingsDir + "/emHdBindings.wasm",
            destinationDir + "/emHdBindings.wasm"
        );
        await fs.copy(
            wasmBindingsDir + "/emHdBindings.worker.js",
            destinationDir + "/emHdBindings.worker.js"
        );
        await fs.copy(
            wasmBindingsDir + "/emHdBindings.data",
            destinationDir + "/emHdBindings.data"
        );
        console.log("NeedleTools-USDViewer: Copied WASM bindings");

        // Copy Three.js Hydra Render Delegate from usd-wasm/src/hydra
        const hydraDir = gitRepoSourceDestDir + "/usd-wasm/src/hydra";
        await fs.copy(
            hydraDir + "/ThreeJsRenderDelegate.js",
            destinationDir + "/ThreeJsRenderDelegate.js"
        );
        console.log("NeedleTools-USDViewer: Copied ThreeJsRenderDelegate");

        // Copy additional assets if they exist
        const publicDir = gitRepoSourceDestDir + "/public";
        const assetsDir = publicDir + "/assets";
        if (await fs.pathExists(assetsDir)) {
            await fs.copy(assetsDir, destinationDir + "/assets");
            console.log("NeedleTools-USDViewer: Copied assets directory");
        }

        // Bundle USD viewer with webpack
        await bundleWithWebpack();

        console.log("NeedleTools-USDViewer: All files copied to destination directory");
    } catch (err) {
        console.error("NeedleTools-USDViewer Build File copy error:", err);
    }
};

// Main function
const main = async () => {
    console.log("=".repeat(60));
    console.log("NeedleTools-USDViewer Viewer Installation");
    console.log("=".repeat(60));

    // Always cleanup previous builds first
    await previousCleanUp();

    // Check if viewer is enabled in config
    if (!checkViewerEnabled(viewerId)) {
        console.log(`NeedleTools-USDViewer: Viewer "${viewerId}" is disabled in viewerConfig.json`);
        console.log("NeedleTools-USDViewer: Skipping installation");
        console.log("=".repeat(60));
        return;
    }

    await gitActions();
    npmBuild();
    await copyFiles();

    console.log("=".repeat(60));
    console.log("NeedleTools-USDViewer: Installation complete!");
    console.log("=".repeat(60));
};

// Run the main function
main();
