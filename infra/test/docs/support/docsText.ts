/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Readers the documentation guard tests share: repository files with normalized line endings, the
 * Docusaurus page tree, page sections, Markdown links and the anchors Docusaurus gives headings, the
 * sidebar, Python source with comments removed, and block-style YAML paths in VAMS_API.yaml.
 */

import * as fs from "fs";
import * as path from "path";

export const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");
export const DOCS = path.join(REPO_ROOT, "documentation", "docusaurus-site", "docs");
export const SIDEBARS = "documentation/docusaurus-site/sidebars.ts";
export const OPENAPI = "documentation/VAMS_API.yaml";

/** File text with line endings normalized, so a CRLF checkout reads the same as an LF one. */
export const read = (rel: string): string =>
    fs.readFileSync(path.join(REPO_ROOT, rel), "utf8").replace(/\r\n/g, "\n");
/** A docs page, by its path under `docs/`. */
export const page = (rel: string): string =>
    read(path.join("documentation", "docusaurus-site", "docs", rel));
export const exists = (rel: string): boolean => fs.existsSync(path.join(REPO_ROOT, rel));

/** The first capture of `re` in `text`, or "" when it does not match. */
export function capture(text: string, re: RegExp): string {
    const m = re.exec(text);
    return m ? m[1] : "";
}

/** Strip Python comments and docstrings so a commented-out declaration is not parsed as live. */
export function pythonCode(source: string): string {
    return source
        .replace(/"""[\s\S]*?"""/g, '""')
        .replace(/'''[\s\S]*?'''/g, "''")
        .split("\n")
        .map((line) => {
            const hash = line.indexOf("#");
            if (hash === -1) return line;
            const before = line.slice(0, hash);
            return (before.match(/"/g) || []).length % 2 === 0 ? before : line;
        })
        .join("\n");
}

/**
 * Every Markdown and MDX page under a docs directory (the whole tree by default), as a path relative
 * to `docs/` with `/` separators.
 */
export function docsPages(dir = ""): string[] {
    return fs.readdirSync(path.join(DOCS, dir), { withFileTypes: true }).flatMap((e) => {
        const rel = dir ? `${dir}/${e.name}` : e.name;
        if (e.isDirectory()) return docsPages(rel);
        return /\.mdx?$/.test(e.name) ? [rel] : [];
    });
}

/** A page section: its heading line up to the next heading of the same or a higher level; "" when absent. */
export function sectionOf(text: string, heading: string): string {
    const lines = text.split("\n");
    const start = lines.indexOf(heading);
    if (start === -1) return "";
    const level = heading.indexOf(" ");
    const end = lines.findIndex((l, i) => i > start && /^#+ /.test(l) && l.indexOf(" ") <= level);
    return lines.slice(start, end === -1 ? lines.length : end).join("\n");
}

/**
 * The text with fenced code blocks blanked, so a `#` line or a link in a fence is not Markdown. A
 * backtick run followed by another backtick on its line is inline code, not a fence (CommonMark).
 * Line numbers are kept: each fenced line becomes an empty line.
 */
export function withoutFences(text: string): string {
    let fence = "";
    return text
        .split("\n")
        .map((line) => {
            const m = /^\s*(`{3,}|~{3,})/.exec(line);
            if (fence) {
                const closes =
                    m !== null &&
                    m[1][0] === fence[0] &&
                    m[1].length >= fence.length &&
                    line.trim() === m[1];
                if (closes) fence = "";
                return "";
            }
            if (!m) return line;
            const info = line.slice(line.indexOf(m[1]) + m[1].length);
            if (m[1][0] === "`" && info.includes("`")) return line;
            fence = m[1];
            return "";
        })
        .join("\n");
}

/**
 * The heading anchors Docusaurus generates for a page: the github-slugger id of each heading's text
 * (backticks, asterisks and link markup removed), suffixed `-1`, `-2` on repeats, or its `{#id}`.
 */
export function headingAnchors(text: string): Set<string> {
    const seen = new Map<string, number>();
    const out = new Set<string>();
    for (const line of withoutFences(text).split("\n")) {
        const m = /^#{1,6}\s+(.*\S)\s*$/.exec(line);
        if (!m) continue;
        const explicit = /\s*\{#([^}]+)\}$/.exec(m[1]);
        if (explicit) {
            out.add(explicit[1]);
            continue;
        }
        const slug = m[1]
            .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
            .replace(/[`*]/g, "")
            .toLowerCase()
            .replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, "")
            .replace(/ /g, "-");
        const n = seen.get(slug) ?? 0;
        seen.set(slug, n + 1);
        out.add(n === 0 ? slug : `${slug}-${n}`);
    }
    return out;
}

export interface DocLink {
    from: string;
    to: string;
    anchor: string;
}

/** Every relative link from one docs page to a page (or a heading on one), outside code. */
export function docLinks(): DocLink[] {
    const out: DocLink[] = [];
    for (const from of docsPages()) {
        const text = withoutFences(page(from)).replace(/`[^`\n]*`/g, "");
        for (const m of text.matchAll(/\]\(([^)\s]+)\)/g)) {
            const target = m[1];
            if (/^[a-z]+:/i.test(target) || target.startsWith("/")) continue;
            const [file, anchor = ""] = target.split("#");
            if (file !== "" && !/\.mdx?$/.test(file)) continue;
            const to =
                file === ""
                    ? from
                    : path.posix.normalize(path.posix.join(path.posix.dirname(from), file));
            out.push({ from, to, anchor });
        }
    }
    return out;
}

/** Each link that names a missing page, or a heading its page lacks, as `from -> to#anchor`. */
export function unresolvedLinks(links: DocLink[]): string[] {
    return links
        .filter(
            (l) =>
                !fs.existsSync(path.join(DOCS, l.to)) ||
                (l.anchor !== "" && !headingAnchors(page(l.to)).has(l.anchor))
        )
        .map((l) => `${l.from} -> ${l.to}${l.anchor ? `#${l.anchor}` : ""}`);
}

/**
 * The doc ids of a sidebar category, in order: the quoted ids of its `items` list up to the bracket
 * that closes it, nested categories included.
 */
export function sidebarCategoryItems(label: string, source = read(SIDEBARS)): string[] {
    const start = source.indexOf(`label: "${label}"`);
    if (start === -1) return [];
    const open = source.indexOf("items: [", start) + "items: ".length;
    let depth = 0;
    let end = open;
    for (; end < source.length; end++) {
        if (source[end] === "[") depth += 1;
        else if (source[end] === "]" && --depth === 0) break;
    }
    // A string that is a property value (`type: "category"`, `label: "..."`) is not a doc id.
    const block = source.slice(open, end);
    return Array.from(block.matchAll(/"([^"]*)"/g))
        .filter((m) => !/\w+:\s*$/.test(block.slice(0, m.index)))
        .map((m) => m[1]);
}

/** The blank-line-separated paragraphs of `text` that contain `needle`. */
export function paragraphsWith(text: string, needle: string): string[] {
    return text.split(/\n\s*\n/).filter((p) => p.includes(needle));
}

/** The JSON code blocks of a page, as their source text. */
export function jsonBlocks(text: string): string[] {
    return Array.from(text.matchAll(/^```json\n([\s\S]*?)^```$/gm), (m) => m[1]);
}

/**
 * For each line of a YAML document, whether it continues a double-quoted scalar opened on an earlier
 * line; such a line may start in any column, so it says nothing about the block structure. A scalar
 * is double-quoted only when its value starts with `"`; a `"` inside a plain or single-quoted scalar
 * opens nothing.
 */
export function yamlContinuations(lines: string[]): boolean[] {
    const unescapedQuote = (s: string): boolean => /"/.test(s.replace(/\\./g, ""));
    let open = false;
    return lines.map((line) => {
        const continues = open;
        if (open) {
            if (unescapedQuote(line)) open = false;
        } else {
            const value = /^\s*(?:-\s+)?(?:[^\s"'#-][^:#]*:\s+|-\s+)?"(.*)$/.exec(line);
            if (value && !unescapedQuote(value[1])) open = true;
        }
        return continues;
    });
}

/**
 * The line index of the mapping key reached by `keys` in a block-style YAML document, each key a
 * direct child of the one before it, or -1. Only plain `key:` lines open a mapping.
 */
export function yamlNode(lines: string[], keys: string[]): number {
    const continuation = yamlContinuations(lines);
    let at = -1;
    let indent = -1;
    for (const key of keys) {
        let found = -1;
        let childIndent = -1;
        for (let i = at + 1; i < lines.length; i++) {
            const line = lines[i];
            if (continuation[i] || line.trim() === "" || line.trim().startsWith("#")) continue;
            const lineIndent = line.search(/\S/);
            if (lineIndent <= indent) break;
            if (childIndent === -1) childIndent = lineIndent;
            if (lineIndent === childIndent && line.trim() === `${key}:`) {
                found = i;
                break;
            }
        }
        if (found === -1) return -1;
        at = found;
        indent = lines[found].search(/\S/);
    }
    return at;
}

/** The lines nested under line `at`, its own line excluded. */
export function yamlBlock(lines: string[], at: number): string[] {
    const continuation = yamlContinuations(lines);
    const indent = lines[at].search(/\S/);
    const out: string[] = [];
    for (let i = at + 1; i < lines.length; i++) {
        const line = lines[i];
        if (!continuation[i] && line.trim() !== "" && line.search(/\S/) <= indent) break;
        out.push(line);
    }
    return out;
}

/**
 * The entries of the mapping reached by `keys`, one level in, in order: each key with the text after
 * its colon ("" for a nested mapping); [] when the path does not exist.
 */
export function yamlPairs(text: string, keys: string[]): Array<[string, string]> {
    const lines = text.split("\n");
    const at = yamlNode(lines, keys);
    if (at === -1) return [];
    const block = yamlBlock(lines, at).filter((l) => l.trim() !== "");
    if (block.length === 0) return [];
    const childIndent = block[0].search(/\S/);
    return block
        .filter((l) => l.search(/\S/) === childIndent)
        .map((l) => /^\s*([^\s:][^:]*):\s*(.*)$/.exec(l))
        .filter((m): m is RegExpExecArray => m !== null)
        .map((m): [string, string] => [m[1], m[2].trim()]);
}

/** The keys of the mapping reached by `keys`, one level in; [] when the path does not exist. */
export function yamlKeys(text: string, keys: string[]): string[] {
    return yamlPairs(text, keys).map(([key]) => key);
}

/** The entries of the mapping reached by `keys`, one level in, by key; {} when the path does not exist. */
export function yamlEntries(text: string, keys: string[]): Record<string, string> {
    return Object.fromEntries(yamlPairs(text, keys));
}

/** `text` with the first line `from` nested under the YAML key reached by `keys` made `to`. */
export function yamlReplace(text: string, keys: string[], from: string, to: string): string {
    const lines = text.split("\n");
    const at = yamlNode(lines, keys);
    if (at === -1) return text;
    const i = yamlBlock(lines, at).findIndex((l) => l.trim() === from);
    if (i === -1) return text;
    lines[at + 1 + i] = lines[at + 1 + i].replace(from, to);
    return lines.join("\n");
}

/** The text nested under the YAML key reached by `keys`; "" when the path does not exist. */
export function yamlText(text: string, keys: string[]): string {
    const lines = text.split("\n");
    const at = yamlNode(lines, keys);
    return at === -1 ? "" : yamlBlock(lines, at).join("\n");
}

/** Every CLAUDE.md file of the Claude Code steering family, outside dependency and scratch trees. */
export function claudeSteeringFiles(dir = ""): string[] {
    const skip = new Set([
        "node_modules",
        ".git",
        "cdk.out",
        "build",
        "dist",
        ".venv",
        "__pycache__",
    ]);
    return fs.readdirSync(path.join(REPO_ROOT, dir), { withFileTypes: true }).flatMap((e) => {
        const rel = dir ? `${dir}/${e.name}` : e.name;
        if (e.isDirectory()) {
            return skip.has(e.name) || (e.name.startsWith(".") && e.name !== ".claude")
                ? []
                : claudeSteeringFiles(rel);
        }
        return e.name === "CLAUDE.md" ? [rel] : [];
    });
}

/** Every document of the Kiro steering family. */
export function kiroSteeringFiles(): string[] {
    const dir = ".kiro/steering";
    return fs
        .readdirSync(path.join(REPO_ROOT, dir))
        .filter((f) => f.endsWith(".md"))
        .map((f) => `${dir}/${f}`);
}
