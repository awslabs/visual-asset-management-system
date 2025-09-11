/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

//npm install simple-git fs-extra
const { simpleGit, SimpleGit, SimpleGitOptions } = require("simple-git");
const { execSync } = require("child_process");
const fs = require("fs-extra");

// Configurations
const gitRepoSourceDestDir = "./customInstalls/online3dviewer/source"; //Relative to base web directory where yarn/npm is run
const gitRepoUrl = "https://github.com/kovacsv/Online3DViewer.git";
const gitRepoCommitHash = "f4260cc7be355b3cfe0fa6cb421d2864bc396133";
const nodeModulesDestDir = "./node_modules/online-3d-viewer"; //Relative to base web directory where yarn/npm is run
//const pluginSourceDestDir = "./src/visualizerPlugin/viewers/Online3dViewerPlugin/source"; //Relative to base web directory where yarn/npm is run
const publicDestinationDir = "./public/online3dviewer"; //Relative to base web directory where yarn/npm is run

// Function to cleanup previous git source and build binaries
const previousCleanUp = async () => {
    try {
        //await fs.rmSync(gitRepoSourceDestDir, { recursive: true, force: true });
        //await fs.rmSync(nodeModulesDestDir + "/build/website", { recursive: true, force: true });
        //await fs.rmSync(nodeModulesDestDir + "/source/website", { recursive: true, force: true });
        //await fs.rmSync(pluginSourceDestDir, { recursive: true, force: true });
        await fs.rmSync(publicDestinationDir, { recursive: true, force: true });
        console.log("Online3DViewer Previous Build Cleanup Complete");
    } catch (err) {
        console.error("Online3DViewer Previous Build Cleanup error:", err);
    }
};

// // Function to perform Git pull and checkout to specific commit
// const gitActions = async () => {
//     try {
//         await simpleGit().clone(gitRepoUrl, gitRepoSourceDestDir);
//         console.log("Online3DViewer Build Git Clone complete");

//         const git = simpleGit({ baseDir: gitRepoSourceDestDir });
//         await git.checkout(gitRepoCommitHash);
//         console.log("Online3DViewer Build Git Checkout Specific Commit complete");
//         console.log("Online3DViewer Build Git complete");
//     } catch (err) {
//         console.error("Online3DViewer Build Git error:", err);
//     }
// };

// // Function to copy minified files to plugin directory for direct integration
// const copyMinifiedFiles = async () => {
//     try {
//         // Create plugin source directory
//         await fs.mkdir(publicDestinationDir, { recursive: true });

//         // Copy minified website files (these have all dependencies bundled)
//         await fs.copy(gitRepoSourceDestDir + "/build/website",  publicDestinationDir);
//         console.log("Online3DViewer Minified website files copied to public plugin directory");

//         // Copy website assets for direct access
//         const websiteAssetsDir = gitRepoSourceDestDir + "/website/assets";
//         if (await fs.pathExists(websiteAssetsDir)) {
//             await fs.mkdir(publicDestinationDir + "/assets", { recursive: true });
//             await fs.copy(websiteAssetsDir,  publicDestinationDir + "/assets");
//             console.log("Online3DViewer Website assets copied to public plugin assets directory");
//         }

//         console.log("Online3DViewer Minified Files copied to plugin directory");
//     } catch (err) {
//         console.error("Online3DViewer Minified Files copy error:", err);
//     }
// };

// Function to copy files to node_modules for compatibility
const copyFiles = async () => {
    try {
        // // Create build/website directory and copy files
        //await fs.mkdir(nodeModulesDestDir + "/website", { recursive: true });
        // await fs.copy(gitRepoSourceDestDir + "/build/website", nodeModulesDestDir + "/build/website");
        // console.log("Online3DViewer Build website files copied to node_modules build directory");

        // // Create source/website directory and copy files
        // await fs.mkdir(nodeModulesDestDir + "/source/website", { recursive: true });
        // await fs.copy(gitRepoSourceDestDir + "/website", nodeModulesDestDir + "/source/website");
        // console.log("Online3DViewer Source website files copied to node_modules source directory");

        //Copy website assets to public
        await fs.copy(nodeModulesDestDir + "/website", publicDestinationDir);

        console.log("Online3DViewer Build Files copied to destination directories");
    } catch (err) {
        console.error("Online3DViewer Build File copy error:", err);
    }
};

// // Function to run NPM install and build on Online3DViewer Source
// const npmBuild = () => {
//     try {
//         execSync("npm install", { cwd: gitRepoSourceDestDir }); //Install dependencies
//         console.log("Online3DViewer Build NPM install complete");
//         execSync("npm run build_website", { cwd: gitRepoSourceDestDir }); //Run build script
//         console.log("Online3DViewer Build NPM build_website complete");
//     } catch (err) {
//         console.error("Online3DViewer Build NPM install/build error:", err);
//     }
// };

// Main function
const main = async () => {
    await previousCleanUp();
    //await gitActions();
    //npmBuild();
    //await copyMinifiedFiles();
    await copyFiles();
};

// Run the main function
main();
