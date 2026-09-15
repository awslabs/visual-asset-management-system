/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * CSP `script-src` SHA-256 sources for the inline <script> blocks in web/index.html.
 *
 * Allowing these by hash rather than by `'unsafe-inline'` means an injected inline script is still
 * blocked, because a hash source only matches the exact script bytes it was computed from.
 *
 * GENERATED — do not hand-edit. A CSP hash covers the exact text content of the element, including
 * indentation, so reformatting web/index.html invalidates these values. Regenerate with:
 *
 *     cd web && npm run build && node scripts/cspInlineScriptHashes.js --ts
 *
 * The digests are of the **LF** form of each block. Line endings are part of the hashed bytes, and
 * index.html is stored in git with LF while a Windows checkout receives CRLF, so the generator
 * folds line endings to LF to keep the value independent of the machine it ran on. The served bytes
 * must be that same form: a bundle built from a CRLF working copy serves CRLF, matches none of
 * these, and the browser then refuses every inline script with nothing failing at build time.
 *
 * `infra/test/cspInlineScriptHashes.test.ts` recomputes the hashes from web/index.html and fails if
 * this list drifts or holds the CRLF form, so both surface as a test failure not a blank page.
 *
 * These hashes are the whole of the inline-script allowance: `generateContentSecurityPolicy()`
 * spreads them into `script-src` for every deployment shape and emits no `'unsafe-inline'` for any
 * configuration, so a value missing here is not covered by a fallback.
 */
export const INDEX_HTML_INLINE_SCRIPT_HASHES: string[] = [
    // <script> — __publicField polyfill (esbuild class-field lowering helper)
    "'sha256-hku/6aM3ooy29j14wba8NHxApTrddxAeTzjgmaQiE+g='",
    // <script type="application/javascript"> — SharedArrayBuffer probe + ResizeObserver error filter
    "'sha256-PtYWwvCy+HPI+Xsv6/nD6B/zQGV1AIHdbHRAS8VntJ0='",
    // <script> — pre-render theme application (prevents light-mode flash)
    "'sha256-ypXICNBilhnt2x+THIkHJ78cCD0lsDnuXLbjgnJIy4o='",
];
