/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

//npm install simple-git fs-extra
const { simpleGit } = require("simple-git");
const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");
const crypto = require("crypto");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");

// Configurations
const viewerId = "potree-viewer";
const gitRepoSourceDestDir = "./customInstalls/potree/source"; //Relative to base web directory where npm is run
const gitRepoUrl = "https://github.com/potree/potree.git";
const gitRepoCommitHash = "5636cd47";
const patchFilePath = "./../Potree-Fork_5636cd47.patch"; //Patch file relative to git source directory

const destinationDir = "./public/viewers/potree_libs"; //Relative to base web directory where npm is run

// Third-party code Potree vendors in its repository and VAMS ships with it (copyFiles copies the
// clone's libs/ folder; the build copies laz-perf.wasm into workers/). Each is attributed in its own
// NOTICE.md row, so the files are checked against the hashes of the versions listed there: a commit
// move that changes them stops the install until the rows are re-checked. Potree 1.8.2 added
// libs/copc, a webpack bundle of copc (which pulls in the laz-perf decoder and the cross-fetch
// browser ponyfill); the versions were identified by comparing the files with the npm releases.
const bundledLibraries = [
    {
        name: "copc",
        repository: "https://github.com/connormanning/copc.js",
        version: "0.0.5-alpha.1",
        license: "MIT",
    },
    {
        name: "laz-perf",
        repository: "https://github.com/hobuinc/laz-perf",
        version: "0.0.5",
        license: "Apache-2.0",
    },
    {
        name: "cross-fetch",
        repository: "https://github.com/lquixada/cross-fetch",
        version: "3.1.5",
        license: "MIT",
    },
];
const bundledLibraryFiles = {
    "libs/copc/index.js": "28cc52c5842bb282dc5fe00760c73af722137fab5cb9318fe86dfa074c79629f",
    "libs/copc/laz-perf.wasm": "b0888b3b852d835131b0aee9ca88a70b4d4858417d8a3a2fa730ad55e183920f",
    "build/potree/workers/laz-perf.wasm":
        "b0888b3b852d835131b0aee9ca88a70b4d4858417d8a3a2fa730ad55e183920f",
};

// Function to cleanup previous git source and build binaries
const previousCleanUp = async () => {
    try {
        await fs.rmSync(gitRepoSourceDestDir, { recursive: true, force: true });
        await fs.rmSync(destinationDir, { recursive: true, force: true });
        console.log("Potree Previous Build Cleanup Complete");
    } catch (err) {
        console.error("Potree Previous Build Cleanup error:", err);
    }
};

// Function to perform Git pull, checkout to specific commit, and apply patch.
// Fails loudly: an unpatched Potree loads point clouds without the VAMS auth headers and never
// rejects a failed load, so a clone, checkout or patch error must stop the build.
const gitActions = async () => {
    try {
        await simpleGit().clone(gitRepoUrl, gitRepoSourceDestDir);
        console.log("Potree Build Git Clone complete");

        const git = simpleGit({ baseDir: gitRepoSourceDestDir });
        await git.checkout(gitRepoCommitHash);
        console.log("Potree Build Git Checkout Specific Commit complete");
        await git.applyPatch(patchFilePath, ["--ignore-whitespace", "--whitespace=fix"]);
        console.log("Potree Build Git Patch Apply complete");
        console.log("Potree Build Git complete");
    } catch (err) {
        console.error("Potree Build Git error:", err);
        throw err;
    }
};

// Function to run NPM install and build on Potree Source
const npmBuild = () => {
    try {
        // The clone has its own package.json, so web/.npmrc does not reach this install: pass the
        // 7-day release-age floor explicitly (#334).
        execSync("npm install --min-release-age=7", {
            cwd: gitRepoSourceDestDir,
            stdio: "inherit",
        }); //Install dependencies
        console.log("Potree Build NPM install complete");
        execSync("npm run build", { cwd: gitRepoSourceDestDir, stdio: "inherit" }); //Run build script
        console.log("Potree Build NPM build complete");
    } catch (err) {
        console.error("Potree Build NPM install/build error:", err);
        throw err;
    }
};

// Throws unless every vendored third-party file matches the version attributed in NOTICE.md.
const verifyBundledLibraries = (sourceDir) => {
    const mismatched = Object.entries(bundledLibraryFiles).filter(([file, sha256]) => {
        const full = path.join(sourceDir, file);
        if (!fs.existsSync(full)) return true;
        return crypto.createHash("sha256").update(fs.readFileSync(full)).digest("hex") !== sha256;
    });
    if (mismatched.length) {
        throw new Error(
            "Potree vendored libraries changed: " +
                mismatched.map(([file]) => file).join(", ") +
                ". Identify the new versions of " +
                bundledLibraries.map((l) => l.name).join(", ") +
                ", update their NOTICE.md rows and bundledLibraryFiles."
        );
    }
};

// Function to copy files to destination directory
const copyFiles = async () => {
    try {
        const builtLib = path.join(gitRepoSourceDestDir, "build/potree/potree.js");
        if (!(await fs.pathExists(builtLib))) {
            throw new Error("Potree build output not found: " + builtLib);
        }
        verifyBundledLibraries(gitRepoSourceDestDir);

        //Create potree directory and copy files in
        await fs.mkdir(destinationDir);
        await fs.mkdir(destinationDir + "/potree");
        await fs.copy(gitRepoSourceDestDir + "/build/potree", destinationDir + "/potree");

        //Copy all subdirectory Potree libs over to base destination directory
        await fs.copy(gitRepoSourceDestDir + "/libs", destinationDir);

        console.log("Potree Build Files copied to destination directory");
    } catch (err) {
        console.error("Potree Build File copy error:", err);
        throw err;
    }
};

// Main function
const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("Potree Viewer Installation");
        console.log("=".repeat(60));

        // Always cleanup previous builds first
        await previousCleanUp();

        // Check if viewer is enabled in config
        if (!checkViewerEnabled(viewerId)) {
            console.log(`Potree: Viewer "${viewerId}" is disabled in viewerConfig.json`);
            console.log("Potree: Skipping installation");
            console.log("=".repeat(60));
            return;
        }

        await gitActions();
        npmBuild();
        await copyFiles();

        console.log("=".repeat(60));
        console.log("Potree: Installation complete!");
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("Potree: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        process.exit(1);
    }
};

module.exports = {
    gitActions,
    npmBuild,
    copyFiles,
    verifyBundledLibraries,
    bundledLibraries,
    bundledLibraryFiles,
    main,
    gitRepoCommitHash,
    patchFilePath,
};

// Run the main function when invoked by postinstall (not when a test requires the module)
if (require.main === module) {
    main();
}
