/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

/**
 * The robotics viewer install (web/customInstalls/robotics/, run by `postinstall`) builds the
 * Lichtblick web app from a pinned upstream commit and rewrites its index.html for the VAMS CSP.
 * The page must load no inline script (the CSP admits inline script only by the hashes of
 * web/index.html's own blocks) and must open <head> with the no-referrer policy, ahead of every
 * request it makes, so no request carries the document URL in its Referer. A build that breaks any
 * of that, or that comes from another commit, must fail the install rather than ship.
 */

import fs from "fs";
import os from "os";
import path from "path";

const { postprocess, postprocessDirectory, REFERRER_META } = jest.requireActual(
    "../../../../customInstalls/robotics/postprocessIndexHtml"
);
const { verifyBuild, verifyCommit, buildEnv, LICHTBLICK_COMMIT, PUBLIC_PATH } = jest.requireActual(
    "../../../../customInstalls/robotics/roboticsViewerInstall"
);

// What the entry bundle carries when overlay/entrypoint.tsx renders the app (minified).
const ENTRY_BUNDLE =
    "rootElement:(0,S.jsx)(P1,{appParameters:{},extensionLoaders:[],enableGlobalCss:!0})";

// The shape of the upstream template (packages/suite-web/src/webpackConfigs.ts at v1.29.1, as
// minified by html-webpack-plugin): charset first, an inline <style>, one inline <script> between
// </head> and <body>, and the deferred bundle.
const UPSTREAM_INDEX = [
    '<!doctype html><html><head><meta charset="utf-8"><title>Lichtblick</title>',
    '<link rel="icon" type="image/png" sizes="32x32" href="favicon-32x32.png"/>',
    '<style id="loading-styles">body { margin: 0; }</style>',
    '<script defer="defer" src="/viewers/robotics/main.0123abcd.js"></script></head>',
    "<script>\n      global = globalThis;\n      globalThis.LICHTBLICK_SUITE_DEFAULT_LAYOUT = [/*PLACEHOLDER*/][0];\n    </script>",
    '<body><div id="root"></div></body></html>',
].join("");

const INLINE_SCRIPT = /<script(?![^>]*\bsrc\s*=)[^>]*>/i;

describe("postprocess (index.html for the VAMS CSP)", () => {
    it("moves the inline script into vams-init.js and leaves none in the page", () => {
        const { html, initJs } = postprocess(UPSTREAM_INDEX);
        expect(INLINE_SCRIPT.test(html)).toBe(false);
        expect(initJs).toContain("global = globalThis;");
        expect(initJs).toContain("LICHTBLICK_SUITE_DEFAULT_LAYOUT");
        expect(html).toContain('<script src="vams-init.js"></script>');
        // The bundle is untouched.
        expect(html).toContain('<script defer="defer" src="/viewers/robotics/main.0123abcd.js">');
    });

    it("puts the no-referrer policy first in <head>, then vams-init.js, before any other element", () => {
        const { html } = postprocess(UPSTREAM_INDEX);
        const head = html.slice(html.indexOf("<head>") + "<head>".length);
        expect(head.startsWith(`${REFERRER_META}<script src="vams-init.js"></script>`)).toBe(true);
        expect(html.indexOf(REFERRER_META)).toBeLessThan(html.indexOf("<link"));
        expect(html.indexOf("vams-init.js")).toBeLessThan(html.indexOf("main.0123abcd.js"));
    });

    it("moves every inline block when there are several, in order", () => {
        const twoBlocks = UPSTREAM_INDEX.replace(
            "</head>",
            '<script type="text/javascript">window.first = 1;</script></head>'
        );
        const { html, initJs } = postprocess(twoBlocks);
        expect(INLINE_SCRIPT.test(html)).toBe(false);
        expect(initJs.indexOf("window.first = 1;")).toBeLessThan(initJs.indexOf("globalThis"));
    });

    it("refuses a page with no inline script: the upstream template changed", () => {
        const { html } = postprocess(UPSTREAM_INDEX);
        expect(() => postprocess(html)).toThrow(/no inline <script> found/);
    });

    it("refuses a page whose inline script survives the rewrite", () => {
        // An unterminated block: the rewrite cannot remove it, and it must not ship.
        const broken = UPSTREAM_INDEX.replace("<body>", "<body><script>window.late = 1;");
        expect(() => postprocess(broken)).toThrow(/survived post-processing/);
    });

    it("refuses a page with no <head>", () => {
        expect(() => postprocess("<html><script>x</script></html>")).toThrow(/no <head>/);
    });
});

describe("the install's build checks", () => {
    const dirs: string[] = [];
    const builtDir = (files: Record<string, string>) => {
        const dir = fs.mkdtempSync(path.join(os.tmpdir(), "robotics-build-"));
        dirs.push(dir);
        for (const [name, body] of Object.entries(files)) {
            fs.mkdirSync(path.dirname(path.join(dir, name)), { recursive: true });
            fs.writeFileSync(path.join(dir, name), body);
        }
        return dir;
    };
    afterAll(() => dirs.forEach((dir) => fs.rmSync(dir, { recursive: true, force: true })));

    it("accepts a post-processed build", () => {
        const dir = builtDir({ "index.html": UPSTREAM_INDEX, "main.0123abcd.js": ENTRY_BUNDLE });
        postprocessDirectory(dir);
        expect(verifyBuild(dir)).toEqual([]);
        expect(fs.readFileSync(path.join(dir, "vams-init.js"), "utf8")).toContain("globalThis");
    });

    it.each([
        // Upstream WebRoot: the IndexedDB extension loaders (installed extensions run on the origin).
        ["WebRoot's loaders", "(0,S.jsx)(P1,{extensionLoaders:n,enableGlobalCss:!0})"],
        ["no loader list", "run();"],
        ["a second loader list", `${ENTRY_BUNDLE};x({extensionLoaders:m})`],
    ])("rejects an entry bundle with %s", (_case, bundle) => {
        const dir = builtDir({ "index.html": UPSTREAM_INDEX, "main.0123abcd.js": bundle });
        postprocessDirectory(dir);
        expect(verifyBuild(dir)).toEqual([
            "main.0123abcd.js does not render the app with an empty extension loader list",
        ]);
    });

    it("rejects a page that loads no entry bundle", () => {
        const dir = builtDir({ "index.html": UPSTREAM_INDEX });
        postprocessDirectory(dir);
        expect(verifyBuild(dir)).toEqual(["index.html loads no main.<hash>.js entry bundle"]);
    });

    it("[control] rejects the build before post-processing", () => {
        const dir = builtDir({ "index.html": UPSTREAM_INDEX });
        const problems = verifyBuild(dir);
        expect(problems).toContain("index.html has an inline <script>");
        expect(problems).toContain("the referrer policy <meta> is not the first element of <head>");
        expect(problems).toContain("vams-init.js is missing");
    });

    it("rejects a shipped source map and a font embedded as a data: URI", () => {
        const dir = builtDir({
            "index.html": UPSTREAM_INDEX,
            "main.0123abcd.js": 'const font = "data:font/woff2;base64,d09GMgABAAAA";',
            "nested/chunk.js.map": "{}",
            "styles.css": "@font-face { src: url(data:application/font-woff;base64,AAAA); }",
        });
        postprocessDirectory(dir);
        const problems = verifyBuild(dir);
        expect(problems).toContain("font embedded as a data: URI in main.0123abcd.js");
        expect(problems).toContain("font embedded as a data: URI in styles.css");
        expect(problems).toContain(`source map shipped: ${path.join("nested", "chunk.js.map")}`);
    });

    it("builds only the pinned commit", () => {
        expect(LICHTBLICK_COMMIT).toMatch(/^[0-9a-f]{40}$/);
        expect(() => verifyCommit(`${LICHTBLICK_COMMIT}\n`)).not.toThrow();
        expect(() => verifyCommit("0".repeat(40))).toThrow(/refusing to build/);
    });

    it("hosts the build where viewerConfig.json's basePath points", () => {
        const config = jest.requireActual("../../config/viewerConfig.json");
        const entry = config.viewers.find((viewer: any) => viewer.id === "robotics-data-viewer");
        expect(entry.customParameters.basePath).toBe(`${PUBLIC_PATH}index.html`);
        // The webpack wrapper's publicPath must match too, or the bundle requests 404.
        const wrapper = fs.readFileSync(
            path.join(
                __dirname,
                "../../../../customInstalls/robotics/overlay/vams.webpack.config.ts"
            ),
            "utf8"
        );
        expect(wrapper).toContain(`const PUBLIC_PATH = "${PUBLIC_PATH}";`);
    });

    it("builds without the host's API_URL and DEV_WORKSPACE, which upstream bakes into the bundle", () => {
        const env = buildEnv({
            API_URL: "https://example.invalid",
            DEV_WORKSPACE: "ws",
            PATH: "/bin",
        });
        expect(env.API_URL).toBe("");
        expect(env.DEV_WORKSPACE).toBe("");
        expect(env.PATH).toBe("/bin");
    });
});

/**
 * A Lichtblick extension is code the app runs in the framed document, which is same-origin with
 * VAMS and can read the signed-in user's tokens. Upstream's WebRoot always adds IndexedDB extension
 * loaders, so the VAMS entrypoint renders upstream's SharedRoot itself with none.
 */
describe("the overlay entrypoint loads no Lichtblick extension", () => {
    const source = fs.readFileSync(
        path.join(__dirname, "../../../../customInstalls/robotics/overlay/entrypoint.tsx"),
        "utf8"
    );
    // The code only: the header comment names the upstream classes the entrypoint avoids.
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

    it("renders its own root, with an empty extension loader list", () => {
        expect(code).toMatch(/rootElement:\s*\(\s*<SharedRoot\b/);
        expect(code.match(/extensionLoaders=\{[^}]*\}/g)).toEqual(["extensionLoaders={[]}"]);
    });

    it("neither renders upstream's WebRoot nor names an extension loader", () => {
        expect(code).not.toMatch(/\bWebRoot\b/);
        expect(code).not.toMatch(/ExtensionLoader/);
    });

    it("replaces the Extensions settings with a notice", () => {
        expect(code).toMatch(/extensionSettings:\s*<ExtensionsUnavailable\s*\/>/);
    });

    it("answers a dropped or chosen .foxe file with a toast inside the app's providers", () => {
        expect(code).toMatch(
            /import \{ EXTENSIONS_UNAVAILABLE, refuseExtensionFiles \} from "\.\/vamsExtensionFiles";/
        );
        expect(code).toMatch(
            /refuseExtensionFiles\(window,\s*\(message\)\s*=>\s*\{\s*enqueueSnackbar\(message/
        );
        expect(code).toMatch(
            /extraProviders=\{\[[\s\S]*<RefuseExtensionFiles key=[^>]*\/>,\s*\]\}/
        );
    });
});

/**
 * Every open starts with the left sidebar on Topics, so the recording's topics are listed as soon
 * as it loads. Upstream opens on the Panel tab and keeps the last chosen tab in browser storage,
 * which would override the default on every later open.
 */
describe("the overlay entrypoint opens the viewer on the Topics tab", () => {
    const source = fs.readFileSync(
        path.join(__dirname, "../../../../customInstalls/robotics/overlay/entrypoint.tsx"),
        "utf8"
    );
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

    it("hands the app its own workspace store through AppContext", () => {
        expect(code).toMatch(
            /<AppContext\.Provider[\s\S]*?value=\{\{[^}]*\bworkspaceStoreCreator,?\s*\}\}/
        );
    });

    it("starts from upstream's defaults and the app's dialog state, then opens Topics", () => {
        const defaults = code.search(
            /\.\.\.makeWorkspaceContextInitialState\(\),\s*\.\.\.initialState,/
        );
        const topics = code.search(
            /left:\s*\{\s*item:\s*"topics",\s*open:\s*true,\s*size:\s*undefined\s*\}/
        );
        expect(defaults).toBeGreaterThan(-1);
        // The Topics default comes after the spreads, so neither can replace it.
        expect(topics).toBeGreaterThan(defaults);
    });

    it("keeps the store out of browser storage, so no earlier choice replaces Topics", () => {
        expect(code).toMatch(/createStore<WorkspaceContextStore>\(\)\(\(\) => state\)/);
        expect(code).not.toMatch(/zustand\/middleware|\bpersist\b/);
        expect(code).not.toMatch(/\b(?:sessionStorage|localStorage)\b/);
    });
});
