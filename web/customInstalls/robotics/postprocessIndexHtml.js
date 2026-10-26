/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Post-processes the built robotics viewer index.html for the VAMS Content Security Policy:
 *  - moves every inline <script> block into vams-init.js, loaded by a classic <script src>. The CSP
 *    admits an inline script only by the hashes of web/index.html's own blocks, so an inline block
 *    in a viewer page never runs;
 *  - puts <meta name="referrer" content="no-referrer"> first in <head>, ahead of every <script> and
 *    <link>, so no request the page makes carries the document URL in its Referer.
 *
 * The deep link (presigned URLs in the URL fragment) is moved into the query string by the VAMS
 * entrypoint in overlay/entrypoint.tsx, after the app has loaded its fonts.
 *
 * Library use: const { postprocess } = require("./postprocessIndexHtml");
 * Command line: node postprocessIndexHtml.js <built viewer directory>
 */

const fs = require("fs");
const path = require("path");

const INIT_SCRIPT_NAME = "vams-init.js";
const REFERRER_META = '<meta name="referrer" content="no-referrer">';

/** An opening <script> tag without a src attribute, i.e. an inline script. */
const INLINE_SCRIPT_OPEN = /<script(?![^>]*\bsrc\s*=)[^>]*>/i;
const INLINE_SCRIPT_BLOCK = /<script(?![^>]*\bsrc\s*=)[^>]*>([\s\S]*?)<\/script\s*>/gi;
const HEAD_OPEN = /<head(\s[^>]*)?>/i;

/**
 * Rewrites the built index.html.
 * @param {string} html - The index.html the upstream build emitted
 * @returns {{html: string, initJs: string}} The rewritten page and the vams-init.js contents
 * @throws {Error} When the page has no <head>, no inline script (the upstream template changed and
 *     needs a review before shipping), or an inline script survives the rewrite
 */
function postprocess(html) {
    if (typeof html !== "string") {
        throw new Error("postprocess: expected the index.html text");
    }
    const headOpen = html.match(HEAD_OPEN);
    if (!headOpen) {
        throw new Error("postprocess: index.html has no <head> element");
    }

    const inlineBlocks = [];
    let rewritten = html.replace(INLINE_SCRIPT_BLOCK, (_block, body) => {
        inlineBlocks.push(body.trim());
        return "";
    });
    if (inlineBlocks.length === 0) {
        throw new Error(
            "postprocess: no inline <script> found; the upstream page template changed, review it before shipping"
        );
    }

    const injected = `${REFERRER_META}<script src="${INIT_SCRIPT_NAME}"></script>`;
    rewritten = rewritten.replace(HEAD_OPEN, (open) => open + injected);

    if (INLINE_SCRIPT_OPEN.test(rewritten)) {
        throw new Error("postprocess: an inline <script> survived post-processing");
    }

    return { html: rewritten, initJs: inlineBlocks.join("\n;\n") + "\n" };
}

/**
 * Rewrites <dir>/index.html in place and writes <dir>/vams-init.js.
 * @param {string} dir - The built viewer directory
 * @returns {number} The number of inline blocks moved
 */
function postprocessDirectory(dir) {
    const htmlPath = path.join(dir, "index.html");
    const { html, initJs } = postprocess(fs.readFileSync(htmlPath, "utf8"));
    fs.writeFileSync(path.join(dir, INIT_SCRIPT_NAME), initJs);
    fs.writeFileSync(htmlPath, html);
    return initJs.split("\n;\n").length;
}

module.exports = { postprocess, postprocessDirectory, INIT_SCRIPT_NAME, REFERRER_META };

if (require.main === module) {
    const dir = process.argv[2];
    if (!dir) {
        console.error("usage: node postprocessIndexHtml.js <built viewer directory>");
        process.exit(2);
    }
    const moved = postprocessDirectory(dir);
    console.log(`post-processed ${path.join(dir, "index.html")}: ${moved} inline block(s) moved`);
}
