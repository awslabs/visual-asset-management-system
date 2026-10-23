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
const { verifyBuild, verifyCommit, LICHTBLICK_COMMIT, PUBLIC_PATH } = jest.requireActual(
    "../../../../customInstalls/robotics/roboticsViewerInstall"
);

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
        const dir = builtDir({ "index.html": UPSTREAM_INDEX, "main.0123abcd.js": "run();" });
        postprocessDirectory(dir);
        expect(verifyBuild(dir)).toEqual([]);
        expect(fs.readFileSync(path.join(dir, "vams-init.js"), "utf8")).toContain("globalThis");
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
});
