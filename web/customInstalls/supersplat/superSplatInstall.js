/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");

// Configuration
const viewerId = "supersplat-viewer";
const SUPERSPLAT_REPO = "https://github.com/playcanvas/supersplat.git";
const SUPERSPLAT_TAG = "v2.27.4"; // pinned stable release; bump deliberately to upgrade
const BASE_HREF = "/viewers/supersplat/"; // MUST match the public hosting sub-path

const cloneDir = path.resolve(__dirname, "src-clone");
const cloneDistDir = path.join(cloneDir, "dist");
const destinationDir = path.resolve(__dirname, "../../public/viewers/supersplat");

const previousCleanUp = async () => {
    try {
        await fs.rm(cloneDir, { recursive: true, force: true });
        await fs.rm(destinationDir, { recursive: true, force: true });
        console.log("SuperSplat: Previous build cleanup complete");
    } catch (err) {
        console.error("SuperSplat: Previous build cleanup error:", err);
    }
};

const cloneRepo = () => {
    console.log(`SuperSplat: Cloning ${SUPERSPLAT_REPO} @ ${SUPERSPLAT_TAG} ...`);
    execSync(`git clone --depth 1 --branch ${SUPERSPLAT_TAG} ${SUPERSPLAT_REPO} "${cloneDir}"`, {
        stdio: "inherit",
    });
    console.log("SuperSplat: Clone complete");
};

const npmInstall = () => {
    console.log("SuperSplat: Installing dependencies (this can take a few minutes)...");
    execSync("npm install", { cwd: cloneDir, stdio: "inherit" });
    console.log("SuperSplat: NPM install complete");
};

// Best-effort: apply any safe (non-breaking) dependency fixes that `npm audit fix`
// can resolve automatically before the bundle is built and packaged. This patches
// quickly-fixable vulnerabilities in SuperSplat's dependency tree (e.g. PlayCanvas
// transitive deps). Intentionally non-fatal: `npm audit fix` exits non-zero when
// unfixable vulnerabilities remain (those require `--force`/manual review, which we
// do NOT apply), and a registry hiccup must not break the viewer build.
const auditFix = () => {
    console.log("SuperSplat: Running npm audit fix (safe fixes only)...");
    try {
        execSync("npm audit fix", { cwd: cloneDir, stdio: "inherit" });
        console.log("SuperSplat: npm audit fix complete");
    } catch (err) {
        console.warn(
            "SuperSplat: npm audit fix reported unresolved/unfixable vulnerabilities " +
                "(continuing with build; no breaking --force fixes applied)."
        );
    }
};

const buildBundle = () => {
    console.log("SuperSplat: Building static bundle with Rollup...");
    // SuperSplat reads process.env.BASE_HREF and substitutes the <base href> placeholder.
    execSync("npm run build", {
        cwd: cloneDir,
        stdio: "inherit",
        env: { ...process.env, BASE_HREF },
    });
    console.log("SuperSplat: Bundle build complete");
};

const copyBundledFiles = async () => {
    if (!(await fs.pathExists(cloneDistDir))) {
        throw new Error("SuperSplat: Build output not found at " + cloneDistDir);
    }
    await fs.mkdir(destinationDir, { recursive: true });
    await fs.copy(cloneDistDir, destinationDir);

    // Preserve MIT attribution alongside the hosted bundle.
    const licenseSrc = path.join(cloneDir, "LICENSE");
    if (await fs.pathExists(licenseSrc)) {
        await fs.copy(licenseSrc, path.join(destinationDir, "THIRD_PARTY_LICENSE_SUPERSPLAT.txt"));
    }
    console.log("SuperSplat: Copied bundle to " + destinationDir);
};

const cleanupClone = async () => {
    try {
        await fs.rm(cloneDir, { recursive: true, force: true });
    } catch (err) {
        console.error("SuperSplat: Clone cleanup error:", err);
    }
};

const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("SuperSplat Installation");
        console.log("=".repeat(60));

        await previousCleanUp();

        if (!checkViewerEnabled(viewerId)) {
            console.log(`SuperSplat: Viewer "${viewerId}" is disabled in viewerConfig.json`);
            console.log("SuperSplat: Skipping installation");
            console.log("=".repeat(60));
            return;
        }

        cloneRepo();
        npmInstall();
        auditFix();
        buildBundle();
        await copyBundledFiles();
        await cleanupClone();

        console.log("=".repeat(60));
        console.log("SuperSplat: Installation complete! Bundle: " + destinationDir);
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("SuperSplat: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        process.exit(1);
    }
};

main();
