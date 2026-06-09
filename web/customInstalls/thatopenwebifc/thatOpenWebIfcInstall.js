/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");

// Configurations
const viewerId = "thatopenwebifc-viewer";
const npmPackageDir = "./customInstalls/thatopenwebifc";
const npmRepoSourceDestDir = "./customInstalls/thatopenwebifc/node_modules";
const bundleSourceDir = "./customInstalls/thatopenwebifc/dist";
const destinationDir = "./public/viewers/thatopenwebifc";

// Function to cleanup previous builds
const previousCleanUp = async () => {
    try {
        await fs.rmSync(npmRepoSourceDestDir, { recursive: true, force: true });
        await fs.rmSync(bundleSourceDir, { recursive: true, force: true });
        await fs.rmSync(destinationDir, { recursive: true, force: true });
        console.log("ThatOpenWebIfc: Previous build cleanup complete");
    } catch (err) {
        console.error("ThatOpenWebIfc: Previous build cleanup error:", err);
    }
};

// Function to run NPM install
const npmInstall = async () => {
    try {
        console.log("ThatOpenWebIfc: Installing dependencies...");
        await execSync("npm install", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("ThatOpenWebIfc: NPM install complete");
    } catch (err) {
        console.error("ThatOpenWebIfc: NPM install error:", err);
        throw err;
    }
};

// Best-effort: apply safe (non-breaking) npm audit fixes before building/packaging.
// Non-fatal — `npm audit fix` exits non-zero when unfixable vulnerabilities remain
// (those need --force/manual review, which we do NOT apply) and a registry hiccup
// must not break the viewer install.
const auditFix = async () => {
    console.log("ThatOpenWebIfc: Running npm audit fix (safe fixes only)...");
    try {
        await execSync("npm audit fix", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("ThatOpenWebIfc: npm audit fix complete");
    } catch (err) {
        console.warn(
            "ThatOpenWebIfc: npm audit fix reported unresolved/unfixable vulnerabilities (continuing)."
        );
    }
};

// Function to build the bundle using webpack
const buildBundle = async () => {
    try {
        console.log("ThatOpenWebIfc: Building bundle with webpack...");
        await execSync("npx webpack", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("ThatOpenWebIfc: Bundle build complete");
    } catch (err) {
        console.error("ThatOpenWebIfc: Bundle build error:", err);
        throw err;
    }
};

// Function to copy ALL bundled files to the destination.
//
// We copy the entire dist/ directory rather than a hand-picked file list. The
// dist contains: the UMD bundle (thatopenwebifc.min.js), the web-ifc WASM
// binaries (web-ifc.wasm, web-ifc-mt.wasm), the standalone Fragments worker
// (thatopenwebifc-fragments-worker.js), AND a content-hashed worker chunk that
// webpack auto-emits because @thatopen/fragments spawns its worker via
// `new Worker(new URL("./worker.mjs", import.meta.url))`. That hashed chunk
// MUST be present at the configured publicPath (/viewers/thatopenwebifc/) or
// the model worker fails at runtime. Copying the whole dir guarantees every
// emitted asset — including future hashed chunks — reaches public/.
const copyBundledFiles = async () => {
    try {
        console.log("ThatOpenWebIfc: Copying bundled files to destination...");

        // Verify the main UMD bundle was produced before copying.
        const bundleFile = path.join(bundleSourceDir, "thatopenwebifc.min.js");
        if (!(await fs.pathExists(bundleFile))) {
            throw new Error("Bundle file not found: " + bundleFile);
        }

        // Create destination directory and copy the entire dist/ into it.
        await fs.mkdir(destinationDir, { recursive: true });
        await fs.copy(bundleSourceDir, destinationDir);

        // Sanity-check that the critical runtime assets made it across.
        const copied = await fs.readdir(destinationDir);
        const hasWasm = copied.some((f) => f.endsWith(".wasm"));
        const hasWorker = copied.some((f) => f.endsWith(".mjs") || f.endsWith("-worker.js"));
        console.log(
            `ThatOpenWebIfc: Copied ${copied.length} file(s). WASM present: ${hasWasm}, worker present: ${hasWorker}`
        );
        if (!hasWasm) {
            console.warn(
                "ThatOpenWebIfc: No .wasm found in dist - IFC parsing will fail at runtime"
            );
        }
        if (!hasWorker) {
            console.warn(
                "ThatOpenWebIfc: No worker file found in dist - model loading will fail at runtime"
            );
        }

        console.log("ThatOpenWebIfc: Files copied to destination directory");
        console.log("ThatOpenWebIfc: Bundle location: " + destinationDir);
    } catch (err) {
        console.error("ThatOpenWebIfc: File copy error:", err);
        throw err;
    }
};

// Main function
const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("That Open Engine (web-ifc) IFC/BIM Viewer Installation");
        console.log("=".repeat(60));

        // Always cleanup previous builds first
        await previousCleanUp();

        // Check if viewer is enabled in config
        if (!checkViewerEnabled(viewerId)) {
            console.log(`ThatOpenWebIfc: Viewer "${viewerId}" is disabled in viewerConfig.json`);
            console.log("ThatOpenWebIfc: Skipping installation");
            console.log("=".repeat(60));
            return;
        }

        await npmInstall();
        await auditFix();
        await buildBundle();
        await copyBundledFiles();

        console.log("=".repeat(60));
        console.log("ThatOpenWebIfc: Installation complete!");
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("ThatOpenWebIfc: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        process.exit(1);
    }
};

// Run the main function
main();
