/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Builds the Robotics Data Viewer: the Lichtblick web app (MPL-2.0) at a pinned tag and commit,
 * compiled from source with the VAMS overlay in ./overlay and served from
 * web/public/viewers/robotics/. Run by the `postinstall` chain in web/package.json.
 *
 * The upstream sources are compiled unmodified. The overlay adds files beside them in the clone:
 * a VAMS entrypoint, two VAMS data sources and a webpack wrapper (see README.md).
 */

const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");
const { postprocessDirectory, INIT_SCRIPT_NAME, REFERRER_META } = require("./postprocessIndexHtml");

// Configuration
const viewerId = "robotics-data-viewer";
const LICHTBLICK_REPO = "https://github.com/lichtblick-suite/lichtblick.git";
const LICHTBLICK_TAG = "v1.29.1"; // pinned release; bump the tag and the commit together
// pragma: allowlist nextline secret
const LICHTBLICK_COMMIT = "95713edb309d77589b130286642ab149995a5f7b"; // the commit v1.29.1 names
const PUBLIC_PATH = "/viewers/robotics/"; // MUST match the publicPath in overlay/vams.webpack.config.ts

// `source/` is ignored by web/.gitignore and the root .prettierignore, so a clone left behind by
// an interrupted install is never committed or formatted.
const cloneDir = path.resolve(__dirname, "source");
const overlayDir = path.resolve(__dirname, "overlay");
const destinationDir = path.resolve(__dirname, "../../public/viewers/robotics");

const BUILD_ENV = {
    CI: "1",
    HUSKY: "0",
    YARN_ENABLE_TELEMETRY: "0",
    COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
    // The upstream desktop workspace depends on Electron, whose install script downloads a ~100 MB
    // binary the web build never uses.
    ELECTRON_SKIP_BINARY_DOWNLOAD: "1",
    NODE_OPTIONS: "--max-old-space-size=4096",
    // Upstream bakes both into APP_CONFIG; a host value adds remote extension and layout calls.
    API_URL: "",
    DEV_WORKSPACE: "",
};

/** The overlay files copied into <clone>/web/src-vams/, the webpack context of the VAMS build. */
const OVERLAY_SOURCES = [
    "entrypoint.tsx",
    "VamsRemoteDataSourceFactory.ts",
    "VamsDownloadDataSourceFactory.ts",
    "ScrubbedIterableSource.ts",
    "vamsSourceSafety.ts",
    "vamsExtensionFiles.ts",
];
const OVERLAY_WEBPACK_CONFIG = "vams.webpack.config.ts";

/** A data: URI carrying a font, which the VAMS CSP (font-src 'self') blocks. */
const FONT_DATA_URI = /data:(?:font\/|application\/(?:x-)?font)/i;

/**
 * overlay/entrypoint.tsx renders the app with no extension loaders, so no Lichtblick extension
 * (code that would run on the VAMS origin) can be installed or loaded. Minified, that is the one
 * `extensionLoaders` property of the entry bundle.
 */
const NO_EXTENSION_LOADERS = "extensionLoaders:[]";
const EXTENSION_LOADERS_PROP = /extensionLoaders:[^,}]+/g;
const ENTRY_SCRIPT = /<script\b[^>]*\bsrc="(?:[^"]*\/)?(main\.[^"/]+\.js)"/i;

/** The environment of the build commands: the host's, with BUILD_ENV taking precedence. */
const buildEnv = (hostEnv = process.env) => ({ ...hostEnv, ...BUILD_ENV });

const run = (command, options = {}) =>
    execSync(command, { stdio: "inherit", ...options, env: buildEnv() });

const elapsed = (start) => `${Math.round((Date.now() - start) / 1000)}s`;

/**
 * Fails unless the cloned HEAD is the pinned commit (a moved tag or a substituted repository).
 * @param {string} actual - `git rev-parse HEAD` of the clone
 * @param {string} [expected] - The pinned commit
 */
function verifyCommit(actual, expected = LICHTBLICK_COMMIT) {
    const head = String(actual).trim();
    if (head !== expected) {
        throw new Error(
            `Robotics: ${LICHTBLICK_TAG} resolved to commit ${head}, expected ${expected}; refusing to build`
        );
    }
}

/**
 * Lists every file under a directory, relative to it.
 * @param {string} dir
 * @returns {string[]}
 */
function listFiles(dir) {
    return fs
        .readdirSync(dir, { recursive: true, withFileTypes: true })
        .filter((entry) => entry.isFile())
        .map((entry) => path.relative(dir, path.join(entry.parentPath ?? entry.path, entry.name)));
}

/**
 * Checks a built viewer directory against what the VAMS CSP and the deployment allow: the
 * post-processed page and its init script exist, the page has no inline script and opens its
 * <head> with the referrer policy, its entry bundle renders the app with no extension loaders, no
 * source map is shipped, and no font is embedded as a data: URI. Returns the problems found (empty
 * when the build is acceptable).
 * @param {string} dir - The built viewer directory
 * @returns {string[]}
 */
function verifyBuild(dir) {
    const problems = [];
    const htmlPath = path.join(dir, "index.html");
    if (!fs.existsSync(htmlPath)) {
        return ["index.html is missing"];
    }
    const html = fs.readFileSync(htmlPath, "utf8");
    if (/<script(?![^>]*\bsrc\s*=)[^>]*>/i.test(html)) {
        problems.push("index.html has an inline <script>");
    }
    const headStart = html.match(/<head(\s[^>]*)?>\s*/i);
    if (
        !headStart ||
        !html.slice(headStart.index + headStart[0].length).startsWith(REFERRER_META)
    ) {
        problems.push("the referrer policy <meta> is not the first element of <head>");
    }
    if (!fs.existsSync(path.join(dir, INIT_SCRIPT_NAME))) {
        problems.push(`${INIT_SCRIPT_NAME} is missing`);
    }
    const entry = html.match(ENTRY_SCRIPT);
    const entryPath = entry ? path.join(dir, entry[1]) : undefined;
    if (!entryPath || !fs.existsSync(entryPath)) {
        problems.push("index.html loads no main.<hash>.js entry bundle");
    } else {
        const loaders = fs.readFileSync(entryPath, "utf8").match(EXTENSION_LOADERS_PROP) ?? [];
        if (loaders.length !== 1 || loaders[0] !== NO_EXTENSION_LOADERS) {
            problems.push(
                `${entry[1]} does not render the app with an empty extension loader list`
            );
        }
    }
    for (const file of listFiles(dir)) {
        if (file.endsWith(".map")) {
            problems.push(`source map shipped: ${file}`);
        } else if (/\.(?:js|css)$/.test(file)) {
            if (FONT_DATA_URI.test(fs.readFileSync(path.join(dir, file), "utf8"))) {
                problems.push(`font embedded as a data: URI in ${file}`);
            }
        }
    }
    return problems;
}

/** The SOURCE.txt written beside the build (MPL-2.0 section 3.2: where the source form is). */
function sourceNotice() {
    return [
        "Robotics Data Viewer (Lichtblick): source of the files in this directory",
        "",
        "These files are a production build of the Lichtblick web app, made by",
        "web/customInstalls/robotics/roboticsViewerInstall.js in the VAMS repository",
        "(https://github.com/awslabs/visual-asset-management-system).",
        "",
        `Upstream repository: ${LICHTBLICK_REPO.replace(/\.git$/, "")}`,
        `Tag:                 ${LICHTBLICK_TAG}`,
        `Commit:              ${LICHTBLICK_COMMIT}`,
        "License:             MPL-2.0 (THIRD_PARTY_LICENSE_LICHTBLICK.txt)",
        "",
        "The Source Code Form of the MPL-2.0 files is the upstream repository at the commit",
        "above; the build compiles those files unmodified. The VAMS-authored files compiled",
        "with them (an entrypoint, two data sources with their helpers, and a webpack",
        "wrapper, Apache-2.0) are in web/customInstalls/robotics/overlay/ of the VAMS",
        "repository, and web/customInstalls/robotics/postprocessIndexHtml.js rewrites",
        "index.html.",
        "",
    ].join("\n");
}

const previousCleanUp = () => {
    fs.rmSync(cloneDir, { recursive: true, force: true });
    fs.rmSync(destinationDir, { recursive: true, force: true });
    console.log("Robotics: Previous build cleanup complete");
};

const checkCorepack = () => {
    try {
        execSync("corepack --version", { stdio: "pipe" });
    } catch {
        throw new Error(
            "Robotics: corepack is not available. Lichtblick builds with the Yarn version its " +
                "package.json pins, through corepack, which Node.js 20 to 24 bundle and Node.js 25 " +
                "and later do not. Use Node.js 22 or 24, or install it with `npm install -g corepack`, " +
                'or set "enabled": false on robotics-data-viewer in viewerConfig.json to skip this viewer.'
        );
    }
};

const cloneRepo = () => {
    console.log(`Robotics: Cloning ${LICHTBLICK_REPO} @ ${LICHTBLICK_TAG} ...`);
    run(
        `git -c advice.detachedHead=false clone --depth 1 --branch ${LICHTBLICK_TAG} ${LICHTBLICK_REPO} "${cloneDir}"`
    );
    verifyCommit(execSync("git rev-parse HEAD", { cwd: cloneDir, encoding: "utf8" }));
    console.log(`Robotics: Clone verified at ${LICHTBLICK_COMMIT}`);
};

const yarnInstall = () => {
    console.log(
        "Robotics: Installing dependencies from the upstream lockfile (several minutes)..."
    );
    run("corepack yarn install --immutable", { cwd: cloneDir });
};

const applyOverlay = () => {
    const vamsSrc = path.join(cloneDir, "web", "src-vams");
    fs.mkdirSync(vamsSrc, { recursive: true });
    for (const file of OVERLAY_SOURCES) {
        fs.copyFileSync(path.join(overlayDir, file), path.join(vamsSrc, file));
    }
    // The upstream web tsconfig (path aliases, strict settings) applies to the overlay unchanged.
    fs.copyFileSync(
        path.join(cloneDir, "web", "src", "tsconfig.json"),
        path.join(vamsSrc, "tsconfig.json")
    );
    fs.copyFileSync(
        path.join(overlayDir, OVERLAY_WEBPACK_CONFIG),
        path.join(cloneDir, "web", OVERLAY_WEBPACK_CONFIG)
    );
};

const buildBundle = () => {
    console.log("Robotics: Building the web app with webpack...");
    run(`corepack yarn webpack --mode production --config web/${OVERLAY_WEBPACK_CONFIG}`, {
        cwd: cloneDir,
    });
    // The overlay only adds files; a changed tracked file means the upstream sources were altered.
    const modified = execSync("git status --porcelain --untracked-files=no", {
        cwd: cloneDir,
        encoding: "utf8",
    }).trim();
    if (modified) {
        throw new Error(`Robotics: the build modified upstream files:\n${modified}`);
    }
};

const finishBundle = () => {
    const builtDir = path.join(cloneDir, "web", ".webpack-vams");
    postprocessDirectory(builtDir);
    const problems = verifyBuild(builtDir);
    if (problems.length > 0) {
        throw new Error(`Robotics: the build is not deployable:\n- ${problems.join("\n- ")}`);
    }
    fs.mkdirSync(destinationDir, { recursive: true });
    fs.cpSync(builtDir, destinationDir, { recursive: true });
    fs.copyFileSync(
        path.join(cloneDir, "LICENSE"),
        path.join(destinationDir, "THIRD_PARTY_LICENSE_LICHTBLICK.txt")
    );
    fs.writeFileSync(path.join(destinationDir, "SOURCE.txt"), sourceNotice());

    const files = listFiles(destinationDir);
    const bytes = files.reduce(
        (total, file) => total + fs.statSync(path.join(destinationDir, file)).size,
        0
    );
    console.log(
        `Robotics: Copied ${files.length} files (${(bytes / 1024 / 1024).toFixed(
            1
        )} MiB) to ${destinationDir}`
    );
};

const cleanupClone = () => {
    try {
        fs.rmSync(cloneDir, { recursive: true, force: true });
    } catch (err) {
        console.error("Robotics: Clone cleanup error:", err);
    }
};

const main = () => {
    try {
        console.log("=".repeat(60));
        console.log("Robotics Data Viewer (Lichtblick) Installation");
        console.log("=".repeat(60));

        previousCleanUp();

        if (!checkViewerEnabled(viewerId)) {
            console.log(`Robotics: Viewer "${viewerId}" is disabled in viewerConfig.json`);
            console.log("Robotics: Skipping installation");
            console.log("=".repeat(60));
            return;
        }

        checkCorepack();
        let start = Date.now();
        cloneRepo();
        yarnInstall();
        const installTime = elapsed(start);
        start = Date.now();
        applyOverlay();
        buildBundle();
        finishBundle();
        const buildTime = elapsed(start);
        cleanupClone();

        console.log("=".repeat(60));
        console.log(`Robotics: Installation complete (install ${installTime}, build ${buildTime})`);
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("Robotics: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        cleanupClone();
        process.exit(1);
    }
};

module.exports = {
    verifyCommit,
    verifyBuild,
    sourceNotice,
    buildEnv,
    LICHTBLICK_COMMIT,
    PUBLIC_PATH,
};

if (require.main === module) {
    main();
}
