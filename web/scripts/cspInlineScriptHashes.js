/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Computes the CSP `script-src` SHA-256 source values for every inline <script> block in an
 * index.html.
 *
 * A CSP hash covers the exact bytes of the element's text content — everything between the
 * opening and closing tag, including leading/trailing whitespace and indentation. That makes the
 * values sensitive to formatting, which is why they are generated rather than hand-maintained:
 * a Prettier run over index.html silently invalidates a literal hash list.
 *
 * Hash the BUILT html, not the source: web/dist/index.html is the document actually served from
 * the web bucket, so its bytes are the ones a browser checks a hash against. Vite injects its
 * bundle tags around the inline blocks but passes their text content through byte for byte, which
 * is what lets infra/test/cspInlineScriptHashes.test.ts recompute the same digests from the source
 * web/index.html.
 *
 * `\r\n` and `\n` are different bytes and therefore different digests, so the emitted values are the
 * digests of each block's LF form. `.gitattributes` pins web/index.html to `text eol=lf`: LF is what
 * a checkout of it holds on every platform, what web/dist/index.html inherits from it, and what
 * every build serves, which makes the LF digest the value to record whatever endings this machine's
 * copy happens to carry. A CRLF copy is reported on stderr and still hashed — the list it produces
 * is the correct one; it is the working tree that is wrong. That is what
 * infra/test/cspInlineScriptHashes.test.ts checks: it compares the recorded values against the
 * file's raw bytes, so it fails on such a checkout, whose own build really would serve bytes that no
 * recorded hash covers.
 *
 * Usage:
 *   node web/scripts/cspInlineScriptHashes.js                 # hashes web/dist/index.html
 *   node web/scripts/cspInlineScriptHashes.js web/index.html  # hashes a specific file
 *   node web/scripts/cspInlineScriptHashes.js --ts            # emit a TypeScript array literal
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

function inlineScriptBodies(html) {
    // Match <script ...>...</script> and keep only those with no src attribute.
    const re = /<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi;
    const bodies = [];
    let m;
    while ((m = re.exec(html)) !== null) {
        const attrs = m[1] || "";
        if (/\bsrc\s*=/i.test(attrs)) continue; // external script: governed by host-source, not a hash
        if (m[2].length === 0) continue; // empty block contributes nothing
        bodies.push({ attrs: attrs.trim(), body: m[2] });
    }
    return bodies;
}

/** The script text with every line ending folded to LF. */
function toLf(text) {
    return text.replace(/\r\n?/g, "\n");
}

function cspHash(body) {
    return "sha256-" + crypto.createHash("sha256").update(body, "utf8").digest("base64");
}

/**
 * Reports a file whose inline blocks carry CRLF line endings, naming the command that converts the
 * working copy. The hashes are still emitted: they are computed from the LF form, which is what every
 * checkout holds and every build serves, so the list is the one to commit either way. Only a bundle
 * built from this copy is affected, and the drift guard is what fails on that.
 */
function warnIfCrlf(file, bodies) {
    if (!bodies.some((b) => b.body.includes("\r"))) return;
    console.error(
        [
            `Warning: the inline <script> blocks in ${file} have CRLF line endings.`,
            ``,
            `The hashes below are still the ones to commit — they are the LF digests, the form that`,
            `\`web/index.html text eol=lf\` in .gitattributes gives every checkout and that every`,
            `build serves. A bundle built from THIS working copy serves CRLF bytes, which those`,
            `hashes do not cover: the browser then refuses every inline script, with nothing failing`,
            `at build or deploy time.`,
            ``,
            `The attribute is applied on checkout, so restore the file from git to convert it.`,
            `Run this from the REPOSITORY ROOT — the paths below are repo-relative, and this script`,
            `is normally run from web/, where they resolve to nothing:`,
            ``,
            `    rm web/index.html && git checkout -- web/index.html`,
            ``,
            `(In cmd.exe: \`del web\\index.html\`, then \`git checkout -- web/index.html\`.) Deleting`,
            `it first is required — \`git add --renormalize\` only rewrites the index, which is`,
            `already LF, and \`git checkout\` alone skips a file it considers up to date. Then`,
            `rebuild: web/dist/index.html inherits its line endings from the source file.`,
        ].join("\n")
    );
}

function main() {
    const args = process.argv.slice(2);
    const asTs = args.includes("--ts");
    const target = args.find((a) => !a.startsWith("--"));
    const file = target
        ? path.resolve(target)
        : path.resolve(__dirname, "..", "dist", "index.html");

    if (!fs.existsSync(file)) {
        console.error(
            `Not found: ${file}\nRun \`npm run build\` in web/ first, or pass a path explicitly.`
        );
        process.exit(1);
    }

    const html = fs.readFileSync(file, "utf8");
    const rawBodies = inlineScriptBodies(html);

    if (rawBodies.length === 0) {
        console.error(`No inline <script> blocks found in ${file}.`);
        process.exit(1);
    }

    warnIfCrlf(file, rawBodies);

    const bodies = rawBodies.map((b) => ({ attrs: b.attrs, body: toLf(b.body) }));
    const hashes = bodies.map((b) => cspHash(b.body));

    if (asTs) {
        console.log("// Generated by web/scripts/cspInlineScriptHashes.js — do not hand-edit.");
        console.log(`// Source: ${path.relative(path.resolve(__dirname, "..", ".."), file)}`);
        console.log("export const INDEX_HTML_INLINE_SCRIPT_HASHES: string[] = [");
        bodies.forEach((b, i) => {
            const label = b.attrs ? `<script ${b.attrs}>` : "<script>";
            const first =
                b.body
                    .split("\n")
                    .map((l) => l.trim())
                    .filter((l) => l && !l.startsWith("//"))[0] || "";
            console.log(`    "'${hashes[i]}'", // ${label} — ${first.slice(0, 60)}`);
        });
        console.log("];");
    } else {
        console.log(`${file}`);
        console.log(`${bodies.length} inline script block(s):\n`);
        bodies.forEach((b, i) => {
            const label = b.attrs ? `<script ${b.attrs}>` : "<script>";
            console.log(`  [${i + 1}] ${label}`);
            console.log(`      '${hashes[i]}'`);
            console.log(`      ${b.body.length} bytes`);
        });
        console.log(`\nCSP script-src fragment:\n  ${hashes.map((h) => `'${h}'`).join(" ")}`);
    }
}

main();
