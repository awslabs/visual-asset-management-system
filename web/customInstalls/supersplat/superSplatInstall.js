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
const SUPERSPLAT_TAG = "v3.7.0"; // pinned stable release; bump deliberately to upgrade
const BASE_HREF = "/viewers/supersplat/"; // MUST match the public hosting sub-path

// SuperSplat v3.7.0 registers "WebMCP" editor tools (playcanvas/supersplat#1081) with the browser's
// document/navigator.modelContext when a browser offers it. The hosted editor is same-origin with
// VAMS, so a browser agent could then drive the editor (import a model from any URL, export the
// scene) inside a signed-in VAMS tab. Upstream has no build flag for this, so the install removes
// the registration from main.ts before building and checks the bundle for leftovers afterwards.
// Each step fails the install if upstream code moves, so a future bump cannot drop it silently.
const WEBMCP_IMPORT = "import { registerWebMCP } from './webmcp';";
const WEBMCP_CALL = "registerWebMCP(editor);";
const WEBMCP_BUNDLE_MARKERS = ["modelContext", "registerTool"];

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

const countOccurrences = (text, needle) => text.split(needle).length - 1;

const disableWebMcp = async () => {
    const mainTs = path.join(cloneDir, "src", "main.ts");
    let source = await fs.readFile(mainTs, "utf8");
    for (const needle of [WEBMCP_IMPORT, WEBMCP_CALL]) {
        const found = countOccurrences(source, needle);
        if (found !== 1) {
            throw new Error(
                `SuperSplat: expected one "${needle}" in src/main.ts, found ${found}; ` +
                    "review the WebMCP registration for this tag before building"
            );
        }
        source = source.replace(needle, "// WebMCP registration removed by the VAMS install");
    }
    await fs.writeFile(mainTs, source);
    console.log("SuperSplat: WebMCP tool registration removed from src/main.ts");
};

const npmInstall = () => {
    console.log("SuperSplat: Installing dependencies (this can take a few minutes)...");
    // The clone has its own package.json, so web/.npmrc does not reach this install: pass the
    // 7-day release-age floor explicitly (#334).
    execSync("npm install --min-release-age=7", { cwd: cloneDir, stdio: "inherit" });
    console.log("SuperSplat: NPM install complete");
};

const assertNoWebMcp = async () => {
    const files = (await fs.readdir(cloneDistDir)).filter((f) => f.endsWith(".js"));
    if (files.length === 0) {
        throw new Error("SuperSplat: no JavaScript found in " + cloneDistDir);
    }
    for (const file of files) {
        const text = await fs.readFile(path.join(cloneDistDir, file), "utf8");
        const marker = WEBMCP_BUNDLE_MARKERS.find((m) => text.includes(m));
        if (marker) {
            throw new Error(`SuperSplat: built ${file} still contains "${marker}" (WebMCP)`);
        }
    }
    console.log("SuperSplat: bundle carries no WebMCP registration");
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
        await disableWebMcp();
        npmInstall();
        buildBundle();
        await assertNoWebMcp();
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
