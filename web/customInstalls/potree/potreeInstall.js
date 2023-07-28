/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

//npm install simple-git fs-extra
const { simpleGit, SimpleGit, SimpleGitOptions } = require("simple-git");
const { execSync } = require("child_process");
const fs = require("fs-extra");

// Configurations
const gitRepoSourceDestDir = "./customInstalls/potree/source"; //Relative to base web directory where yarn/npm is run
const gitRepoUrl = "https://github.com/potree/potree.git";
const gitRepoCommitHash = "f6ac2d3b";
const patchFilePath = "./../Potree-Fork_f6ac2d3b.patch"; //Patch file relative to git source directory

const destinationDir = "./public/potree_libs"; //Relative to base web directory where yarn/npm is run

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

// Function to perform Git pull, checkout to specific commit, and apply patch
const gitActions = async () => {
    try {
        await simpleGit().clone(gitRepoUrl, gitRepoSourceDestDir);
        console.log("Potree Build Git Clone complete");

        const git = simpleGit({ baseDir: gitRepoSourceDestDir });
        await git.checkout(gitRepoCommitHash);
        console.log("Potree Build Git Checkout Specific Commit complete");
        await git.applyPatch(patchFilePath, "--whitespace=fix");
        console.log("Potree Build Git Patch Apply complete");
        console.log("Potree Build Git complete");
    } catch (err) {
        console.error("Potree Build Git error:", err);
    }
};

// Function to run NPM install and build on Potree Source
const npmBuild = () => {
    try {
        execSync("npm install", { cwd: gitRepoSourceDestDir }); //Install dependencies
        console.log("Potree Build NPM install complete");
        execSync("npm run build", { cwd: gitRepoSourceDestDir }); //Run build script
        console.log("Potree Build NPM build complete");
    } catch (err) {
        console.error("Potree Build NPM install/build error:", err);
    }
};

// Function to copy files to destination directory
const copyFiles = async () => {
    try {
        //Create potree directory and copy files in
        await fs.mkdir(destinationDir);
        await fs.mkdir(destinationDir + "/potree");
        await fs.copy(gitRepoSourceDestDir + "/build/potree", destinationDir + "/potree");

        //Copy all subdirectory Potree libs over to base destination directory
        await fs.copy(gitRepoSourceDestDir + "/libs", destinationDir);

        console.log("Potree Build Files copied to destination directory");
    } catch (err) {
        console.error("Potree Build File copy error:", err);
    }
};

// Main function
const main = async () => {
    await previousCleanUp();
    await gitActions();
    npmBuild();
    await copyFiles();
};

// Run the main function
main();
