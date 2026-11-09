/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");
const {
    FFLATE_PUBLIC_PATH,
    rewriteRuntimeFetches,
    findReachableCdnUrls,
} = require("./runtimeFetches");

// Configurations
const viewerId = "gaussian-splat-viewer-babylonjs";
const npmPackageDir = "./customInstalls/babylonjs";
const npmRepoSourceDestDir = "./customInstalls/babylonjs/node_modules";
const bundleSourceDir = "./customInstalls/babylonjs/dist";
const destinationDir = "./public/viewers/babylonjs";

// Function to cleanup previous builds
const previousCleanUp = async () => {
    try {
        await fs.rmSync(npmRepoSourceDestDir, { recursive: true, force: true });
        await fs.rmSync(bundleSourceDir, { recursive: true, force: true });
        await fs.rmSync(destinationDir, { recursive: true, force: true });
        console.log("BabylonJS: Previous build cleanup complete");
    } catch (err) {
        console.error("BabylonJS: Previous build cleanup error:", err);
    }
};

// Function to run NPM install
const npmInstall = async () => {
    try {
        console.log("BabylonJS: Installing dependencies...");
        await execSync("npm install", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("BabylonJS: NPM install complete");
    } catch (err) {
        console.error("BabylonJS: NPM install error:", err);
        throw err;
    }
};

// Function to build the bundle using webpack
const buildBundle = async () => {
    try {
        console.log("BabylonJS: Building bundle with webpack...");
        await execSync("npx webpack", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("BabylonJS: Bundle build complete");
    } catch (err) {
        console.error("BabylonJS: Bundle build error:", err);
        throw err;
    }
};

// Function to copy bundled files to destination directory
const copyBundledFiles = async () => {
    try {
        console.log("BabylonJS: Copying bundled files to destination...");

        // Create destination directory
        await fs.mkdir(destinationDir, { recursive: true });

        // Copy all files from dist directory (includes main bundle and any chunks)
        if (await fs.pathExists(bundleSourceDir)) {
            await fs.copy(bundleSourceDir, destinationDir);
            console.log("BabylonJS: Copied all bundle files from dist directory");
        } else {
            throw new Error("Bundle directory not found: " + bundleSourceDir);
        }

        await vendorRuntimeFetches();

        console.log("BabylonJS: Files copied to destination directory");
        console.log("BabylonJS: Bundle location: " + destinationDir);
    } catch (err) {
        console.error("BabylonJS: File copy error:", err);
        throw err;
    }
};

// Point the splat loader's runtime fetches at same-origin files (or turn them off), then fail the
// install if any unpkg/jsDelivr URL the viewer could reach is still in the published files. See
// runtimeFetches.js for why each default is rewritten.
const vendorRuntimeFetches = async () => {
    const fflateSource = path.join(npmRepoSourceDestDir, "fflate", "umd", "index.js");
    if (!(await fs.pathExists(fflateSource))) {
        // Fail loudly rather than ship a fallback URL that points at a missing file.
        throw new Error(
            `BabylonJS: fflate UMD build not found at ${fflateSource}. fflate is a dependency of ` +
                "this installer; the splat loader's same-origin fallback cannot be vendored without it."
        );
    }
    const fflateDest = path.join(
        destinationDir,
        FFLATE_PUBLIC_PATH.replace(/^\/viewers\/babylonjs\//, "")
    );
    await fs.mkdir(path.dirname(fflateDest), { recursive: true });
    await fs.copy(fflateSource, fflateDest);

    const mainBundle = path.join(destinationDir, "babylonjs.bundle.js");
    const { bundle, applied } = rewriteRuntimeFetches(await fs.readFile(mainBundle, "utf8"));
    await fs.writeFile(mainBundle, bundle, "utf8");

    const leftovers = [];
    for (const name of await fs.readdir(destinationDir)) {
        if (!name.endsWith(".js")) continue;
        const source = await fs.readFile(path.join(destinationDir, name), "utf8");
        for (const url of findReachableCdnUrls(source)) leftovers.push(`${name}: ${url}`);
    }
    if (leftovers.length) {
        throw new Error(
            "BabylonJS: CDN references the viewer could fetch remain in the bundle:\n  " +
                leftovers.join("\n  ")
        );
    }

    console.log("BabylonJS: Runtime fetches vendored (no CDN fetch at run time):");
    for (const line of applied) console.log("  - " + line);
};

// Main function
const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("BabylonJS Installation");
        console.log("=".repeat(60));

        // Always cleanup previous builds first
        await previousCleanUp();

        // Check if viewer is enabled in config
        if (!checkViewerEnabled(viewerId)) {
            console.log(`BabylonJS: Viewer "${viewerId}" is disabled in viewerConfig.json`);
            console.log("BabylonJS: Skipping installation");
            console.log("=".repeat(60));
            return;
        }

        await npmInstall();
        await buildBundle();
        await copyBundledFiles();

        console.log("=".repeat(60));
        console.log("BabylonJS: Installation complete!");
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("BabylonJS: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        process.exit(1);
    }
};

// Run the main function
main();
