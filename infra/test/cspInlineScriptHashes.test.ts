/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as fs from "fs";
import * as path from "path";
import * as crypto from "crypto";
import { INDEX_HTML_INLINE_SCRIPT_HASHES } from "../lib/helper/cspInlineScriptHashes";

/**
 * Guards the CSP inline-script hash list against drift, and against the line-ending form of the
 * bytes it was generated from.
 *
 * A CSP hash covers the exact text content of a <script> element, indentation included, so any edit
 * to an inline block in web/index.html — even a reformat — invalidates the corresponding hash. When
 * that happens the browser silently refuses to run the script and the app breaks in a way that no
 * other test observes. This recomputes the hashes from source so the failure surfaces here instead.
 *
 * The recomputation reads the file's RAW bytes, line endings included, because that is the only
 * form that predicts anything: a build serves the bytes that are on disk, so hashing the bytes on
 * disk is what answers "will a bundle built from this checkout run its inline scripts". Folding line
 * endings before hashing answers a different and weaker question, and passes on a checkout whose
 * build output the recorded hashes do not cover.
 *
 * `.gitattributes` pins web/index.html to `text eol=lf`, so those raw bytes are LF on every
 * platform and the expected values are the same everywhere. A clone made before that rule still
 * holds CRLF in its working tree until the file is restored from git, because the attribute is
 * applied on checkout. That checkout fails here by design — a bundle built from it really would
 * serve unrunnable inline scripts — so `crlfCheckoutRemedy` names the command that converts the
 * working copy instead of leaving a bare digest mismatch behind.
 *
 * Two properties are asserted, and they catch different faults:
 *
 *   1. the recorded digests are of the bytes web/index.html actually holds; and
 *   2. no recorded digest is the CRLF form of its block, which is what a list regenerated on a CRLF
 *      working tree contains and which matches nothing any build serves.
 *
 * Hashing web/index.html rather than web/dist/index.html is sound for the inline blocks: Vite
 * injects its bundle tags around them but passes their text content through byte for byte, so the
 * blocks' digests are identical in source and build output. The source file is also always present,
 * whereas dist/ exists only after a build.
 */

const INDEX_HTML = path.join(__dirname, "..", "..", "web", "index.html");

function inlineScriptBodies(html: string): { attrs: string; body: string }[] {
    const re = /<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi;
    const out: { attrs: string; body: string }[] = [];
    let m: RegExpExecArray | null;
    while ((m = re.exec(html)) !== null) {
        const attrs = (m[1] || "").trim();
        if (/\bsrc\s*=/i.test(attrs)) continue; // external script — matched by host-source, not a hash
        if (m[2].length === 0) continue;
        out.push({ attrs, body: m[2] });
    }
    return out;
}

/** The script text with every line ending folded to LF. */
function toLf(text: string): string {
    return text.replace(/\r\n?/g, "\n");
}

/** The script text with every line ending expanded to CRLF. */
function toCrlf(text: string): string {
    return toLf(text).replace(/\n/g, "\r\n");
}

function cspHash(body: string): string {
    return "'sha256-" + crypto.createHash("sha256").update(body, "utf8").digest("base64") + "'";
}

describe("CSP inline script hashes", () => {
    const html = fs.readFileSync(INDEX_HTML, "utf8");
    const bodies = inlineScriptBodies(html);
    const rawHashes = bodies.map((b) => cspHash(b.body));
    const lfHashes = bodies.map((b) => cspHash(toLf(b.body)));
    const crlfHashes = bodies.map((b) => cspHash(toCrlf(b.body)));
    const checkoutIsCrlf = bodies.some((b) => b.body.includes("\r"));

    const crlfCheckoutRemedy =
        "web/index.html is checked out with CRLF line endings, so its raw bytes hash differently " +
        "from the recorded values. The `web/index.html text eol=lf` rule in .gitattributes is " +
        "applied on checkout, so restore the file from git to convert the working copy. Run this " +
        "FROM THE REPOSITORY ROOT — the paths are repo-relative and this suite runs from infra/, " +
        "where they resolve to nothing: " +
        "`rm web/index.html && git checkout -- web/index.html` (in cmd.exe: `del web\\index.html`, " +
        "then `git checkout -- web/index.html`). Deleting it first is required — " +
        "`git add --renormalize` only rewrites the index, which is already LF, and `git checkout` " +
        "alone skips a file it considers up to date. The recorded hashes need no change: they are " +
        "the LF digests git stores and every build serves. A bundle built from a CRLF working copy " +
        "serves CRLF bytes, which the CSP does not cover, and the browser then refuses every " +
        "inline script with nothing failing at build or deploy time.";

    /** A drift list, with the line-ending remedy named when that is what produced it. */
    function withRemedy(drift: string[]): string[] {
        return drift.length > 0 && checkoutIsCrlf ? [crlfCheckoutRemedy, ...drift] : drift;
    }

    test("web/index.html contains inline script blocks to hash", () => {
        // Positive control: if this ever reads 0, the regex or the file moved and every other
        // assertion below would pass vacuously.
        expect(bodies.length).toBeGreaterThan(0);
    });

    test("web/index.html is checked out with LF line endings", () => {
        // Declared ahead of the digest comparisons so a CRLF working tree reports its own cause
        // first. Without it the raw-bytes comparisons below fail as bare base64 mismatches, which
        // reads as content drift and invites regenerating a list that is already correct.
        expect(checkoutIsCrlf ? [crlfCheckoutRemedy] : []).toEqual([]);
    });

    test("the hash list covers every inline script in web/index.html", () => {
        expect(INDEX_HTML_INLINE_SCRIPT_HASHES).toHaveLength(bodies.length);
    });

    test("every inline script's computed hash is present in the list", () => {
        const missing = rawHashes.filter((h) => !INDEX_HTML_INLINE_SCRIPT_HASHES.includes(h));
        expect(withRemedy(missing)).toEqual([]);
    });

    test("the list contains no stale hashes", () => {
        const stale = INDEX_HTML_INLINE_SCRIPT_HASHES.filter((h) => !rawHashes.includes(h));
        expect(withRemedy(stale)).toEqual([]);
    });

    test("each block's LF and CRLF digests differ", () => {
        // Control for the CRLF assertion below. It asserts the absence of a set of values; if any
        // of those values coincided with the LF digest the assertion would be unsatisfiable, and if
        // a block were single-line the two forms would be equal and the check vacuous for it. Every
        // block in this file spans multiple lines, so every pair must differ.
        for (let i = 0; i < bodies.length; i++) {
            expect(crlfHashes[i]).not.toEqual(lfHashes[i]);
        }
    });

    test("no recorded hash is the CRLF form of its block", () => {
        // Catches a list regenerated on a CRLF working tree. Those digests match nothing a Linux,
        // CodeBuild or macOS build serves, the CSP then blocks every inline script, and no build
        // step fails. Complementary to the raw-bytes comparison above rather than implied by it:
        // this one holds on every platform, including the CRLF one such a list is generated on,
        // where the raw comparison is instead reporting the checkout.
        const crlfForm = INDEX_HTML_INLINE_SCRIPT_HASHES.filter((h) => crlfHashes.includes(h));
        expect(crlfForm).toEqual([]);
    });

    test("the recorded hashes are the LF form of every block", () => {
        // Separates the two ways the raw-bytes comparison can fail. On an LF checkout raw and LF
        // are the same bytes and this restates it; on a CRLF checkout it still holds, which pins
        // the failure to the working tree's line endings and clears the list of content drift. If
        // both fail together, an inline block really was edited without regenerating the list.
        expect([...INDEX_HTML_INLINE_SCRIPT_HASHES].sort()).toEqual([...lfHashes].sort());
    });

    test("hashes are well-formed CSP sha256 source expressions", () => {
        for (const h of INDEX_HTML_INLINE_SCRIPT_HASHES) {
            expect(h).toMatch(/^'sha256-[A-Za-z0-9+/]{43}='$/);
        }
    });

    test("a modified script body does not match its recorded hash", () => {
        // Negative control: proves the hash is actually sensitive to content, so the assertions
        // above would fail if a block changed rather than silently still matching.
        const mutated = cspHash(bodies[0].body + "\n// changed");
        expect(INDEX_HTML_INLINE_SCRIPT_HASHES).not.toContain(mutated);
    });
});
