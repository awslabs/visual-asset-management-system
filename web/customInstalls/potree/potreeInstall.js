/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

//npm install simple-git fs-extra
const { simpleGit } = require("simple-git");
const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");

// Configurations
const viewerId = "potree-viewer";
const gitRepoSourceDestDir = "./customInstalls/potree/source"; //Relative to base web directory where npm is run
const gitRepoUrl = "https://github.com/potree/potree.git";
const gitRepoCommitHash = "5636cd47";
const patchFilePath = "./../Potree-Fork_5636cd47.patch"; //Patch file relative to git source directory

const destinationDir = "./public/viewers/potree_libs"; //Relative to base web directory where npm is run

// The cloned Potree source sits outside web/, so web/.npmrc does not reach its npm install. Pass the
// release-age floor on the command line instead (#334): no dependency younger than 7 days.
const npmInstallCommand = "npm install --min-release-age=7";

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
        execSync(npmInstallCommand, { cwd: gitRepoSourceDestDir, stdio: "inherit" }); //Install dependencies
        console.log("Potree Build NPM install complete");
        execSync("npm run build", { cwd: gitRepoSourceDestDir, stdio: "inherit" }); //Run build script
        console.log("Potree Build NPM build complete");
    } catch (err) {
        console.error("Potree Build NPM install/build error:", err);
        throw err;
    }
};

// Function to copy files to destination directory
const copyFiles = async () => {
    try {
        const builtLib = path.join(gitRepoSourceDestDir, "build/potree/potree.js");
        if (!(await fs.pathExists(builtLib))) {
            throw new Error("Potree build output not found: " + builtLib);
        }

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
    main,
    gitRepoCommitHash,
    patchFilePath,
    npmInstallCommand,
};

// Run the main function when invoked by postinstall (not when a test requires the module)
if (require.main === module) {
    main();
}
