/* Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
   SPDX-License-Identifier: Apache-2.0 */

/**
 * Pin audit for the Splat Toolbox container Dockerfile.
 *
 * The Dockerfile is upstream-owned: it is gitignored and rewritten from the pinned upstream commit on
 * every synth, so its third-party sources are not visible in a diff and a hand edit does not survive.
 * The audit records which of those sources resolve at image-build time rather than at a fixed revision,
 * and fails the synth when that set changes in either direction — a new unrecorded unpinned source, or
 * a recorded one upstream has since pinned. Both directions matter: the second is what keeps the record
 * from silently going stale.
 *
 * The audit does not pin anything. Rewriting upstream's own clone commands and choosing revisions that
 * cannot be shown to compile without a GPU image build is separate work.
 */

/** A source whose contents are decided at image-build time rather than by a fixed revision. */
export interface UnpinnedSource {
    kind: "git-clone" | "pip-git" | "raw-ref" | "download";
    /** As written in the Dockerfile, with variables left unexpanded so a version bump is not a change. */
    url: string;
    line: number;
    /** The revision the source expresses, or "" when it expresses none. */
    ref: string;
}

/**
 * The sources the pinned upstream commit resolves at build time.
 *
 * Derived by running `auditDockerfilePins` over the synced Dockerfile, not written by hand. Each entry
 * is `"<kind> <url>"`; `assertRecordedPinPosture` compares the audit's output against this set exactly.
 */
export const RECORDED_UNPINNED_SOURCES: readonly string[] = [
    "download https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip",
    "download https://deb.nodesource.com/setup_${NODE_VERSION}.x",
    "git-clone https://github.com/Anonym0u3/AttentiveEraser.git",
    "git-clone https://github.com/KevinXu02/splatfacto-w",
    "git-clone https://github.com/ZachMckennedyFWig/ColmapFaissVocabTrees.git",
    "git-clone https://github.com/maturk/dn-splatter.git",
    "pip-git https://github.com/KevinXu02/splatfacto-w",
    "pip-git https://github.com/NVlabs/tiny-cuda-nn.git",
    "pip-git https://github.com/cvg/LightGlue.git",
    "raw-ref https://raw.githubusercontent.com/Anonym0u3/AttentiveEraser/master/pipelines/pipeline_stable_diffusion_xl_attentive_eraser.py",
    "raw-ref https://raw.githubusercontent.com/Anonym0u3/AttentiveEraser/master/pipelines/pipeline_stable_diffusion_xl_attentive_eraser_inversion.py",
];

/** A 40-character object name, or a version-shaped tag. Both are immutable once published. */
const COMMIT_REF = /^[0-9a-f]{40}$/i;
const VERSION_REF = /^v?\d+(?:\.\d+)*$/;

/** Collects `ENV`/`ARG` declarations so a `${VAR}` revision can be classified by its value. */
function collectVariables(content: string): Map<string, string> {
    const variables = new Map<string, string>();
    const assigned =
        /^\s*(?:ENV|ARG)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))/gm;
    const spaced = /^\s*ENV\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:"([^"]*)"|'([^']*)'|(\S+))\s*$/gm;
    for (const pattern of [assigned, spaced]) {
        let match: RegExpExecArray | null;
        while ((match = pattern.exec(content)) !== null) {
            const value = match[2] ?? match[3] ?? match[4] ?? "";
            if (!variables.has(match[1])) {
                variables.set(match[1], value);
            }
        }
    }
    return variables;
}

function expand(text: string, variables: Map<string, string>): string {
    let expanded = text;
    // Three passes, because a version variable is often composed from another one.
    for (let pass = 0; pass < 3; pass++) {
        expanded = expanded.replace(
            /\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g,
            (whole, braced, bare) => variables.get(braced ?? bare) ?? whole
        );
    }
    return expanded;
}

function isPinnedRef(rawRef: string, variables: Map<string, string>): boolean {
    const ref = expand(rawRef, variables)
        .trim()
        .replace(/^['"]|['"]$/g, "");
    return COMMIT_REF.test(ref) || VERSION_REF.test(ref);
}

interface PhysicalLine {
    n: number;
    text: string;
}

/**
 * Groups continued lines into one logical instruction. A `git clone` and the `git reset --hard` that
 * pins it are separate lines of the same `RUN`, and several clones share one `RUN`, so pinning has to
 * be decided per instruction in source order rather than per line.
 */
function groupInstructions(content: string): PhysicalLine[][] {
    const instructions: PhysicalLine[][] = [];
    let current: PhysicalLine[] = [];
    content.split(/\r?\n/).forEach((text, index) => {
        current.push({ n: index + 1, text });
        if (!/\\\s*$/.test(text)) {
            instructions.push(current);
            current = [];
        }
    });
    if (current.length > 0) {
        instructions.push(current);
    }
    return instructions;
}

interface CloneEvent {
    type: "clone";
    at: number;
    line: number;
    url: string;
    branch: string;
}

interface ResetEvent {
    type: "reset";
    at: number;
    line: number;
    ref: string;
}

/** `git clone` accepts its flags on either side of the URL, so the URL is found by shape. */
function readCloneEvents(line: PhysicalLine): CloneEvent[] {
    const events: CloneEvent[] = [];
    const clone = /git\s+clone\b/g;
    let match: RegExpExecArray | null;
    while ((match = clone.exec(line.text)) !== null) {
        const rest = line.text.slice(match.index + match[0].length).split("&&")[0];
        const tokens = rest.split(/\s+/).filter((token) => token.length > 0 && token !== "\\");
        let url = "";
        let branch = "";
        tokens.forEach((token, index) => {
            if (!url && /^(?:https?:\/\/|git@|ssh:\/\/)/.test(token)) {
                url = token;
            }
            if ((token === "--branch" || token === "-b") && index + 1 < tokens.length) {
                branch = tokens[index + 1];
            }
            const inline = /^--branch=(.+)$/.exec(token);
            if (inline) {
                branch = inline[1];
            }
        });
        if (url) {
            events.push({ type: "clone", at: match.index, line: line.n, url, branch });
        }
    }
    return events;
}

function readResetEvents(line: PhysicalLine): ResetEvent[] {
    const events: ResetEvent[] = [];
    const reset = /git\s+(?:-C\s+\S+\s+)?reset\s+--hard\s+(\S+)/g;
    let match: RegExpExecArray | null;
    while ((match = reset.exec(line.text)) !== null) {
        events.push({ type: "reset", at: match.index, line: line.n, ref: match[1] });
    }
    return events;
}

/**
 * A clone is pinned by a `--branch` naming an immutable revision, or by the next `git reset --hard` in
 * the same instruction. A reset belongs to the most recent unresolved clone, which is what separates
 * two clones sharing one `RUN` where only the first is reset.
 */
function auditClones(
    instruction: PhysicalLine[],
    variables: Map<string, string>,
    found: UnpinnedSource[]
): void {
    let pending: CloneEvent | undefined;
    const settle = (clone: CloneEvent, ref: string) => {
        if (!isPinnedRef(ref, variables)) {
            found.push({ kind: "git-clone", url: clone.url, line: clone.line, ref });
        }
    };
    for (const line of instruction) {
        const events = [...readCloneEvents(line), ...readResetEvents(line)].sort(
            (a, b) => a.at - b.at
        );
        for (const event of events) {
            if (event.type === "clone") {
                if (pending) {
                    settle(pending, pending.branch);
                    pending = undefined;
                }
                if (event.branch && isPinnedRef(event.branch, variables)) {
                    continue;
                }
                pending = event;
            } else if (pending) {
                settle(pending, event.ref);
                pending = undefined;
            }
        }
    }
    if (pending) {
        settle(pending, pending.branch);
    }
}

/** `pip install git+<url>` is pinned by an `@<revision>` on the URL, never by a following reset. */
function auditPipGitRefs(
    line: PhysicalLine,
    variables: Map<string, string>,
    found: UnpinnedSource[]
): void {
    const pipGit = /git\+(https?:\/\/[^\s'"`;]+)/g;
    let match: RegExpExecArray | null;
    while ((match = pipGit.exec(line.text)) !== null) {
        const url = match[1].split("#")[0].replace(/[,)\]]+$/, "");
        const atRef = /@([^@/]+)$/.exec(url);
        const ref = atRef ? atRef[1] : "";
        if (!isPinnedRef(ref, variables)) {
            found.push({ kind: "pip-git", url: url.replace(/@[^@/]+$/, ""), line: line.n, ref });
        }
    }
}

/** A raw content URL carries its revision as the third path segment. */
function auditRawRefs(
    line: PhysicalLine,
    variables: Map<string, string>,
    found: UnpinnedSource[]
): void {
    const raw = /https:\/\/raw\.githubusercontent\.com\/[^\s'"`),]+/g;
    let match: RegExpExecArray | null;
    while ((match = raw.exec(line.text)) !== null) {
        const url = match[0];
        const segments = url.split("/").slice(3);
        const ref = segments.length >= 3 ? segments[2] : "";
        if (!isPinnedRef(ref, variables)) {
            found.push({ kind: "raw-ref", url, line: line.n, ref });
        }
    }
}

/**
 * A downloaded archive or installer is treated as pinned when its URL carries a dotted version or a
 * commit-shaped name. That is a heuristic on the URL rather than a revision the audit can resolve, so a
 * new download whose URL happens to contain a dotted number reads as pinned — the set comparison is
 * what carries the guarantee, not this classification.
 */
function auditDownloads(
    line: PhysicalLine,
    variables: Map<string, string>,
    found: UnpinnedSource[]
): void {
    if (!/\b(?:wget|curl)\b/.test(line.text)) {
        return;
    }
    const urls = /https?:\/\/[^\s'"`\\]+/g;
    let match: RegExpExecArray | null;
    while ((match = urls.exec(line.text)) !== null) {
        const url = match[0].replace(/[,)\];]+$/, "");
        const expanded = expand(url, variables);
        if (/\d+\.\d+/.test(expanded) || /[0-9a-f]{40}/i.test(expanded)) {
            continue;
        }
        found.push({ kind: "download", url, line: line.n, ref: "" });
    }
}

/** Every source in the Dockerfile whose contents are decided at image-build time. */
export function auditDockerfilePins(content: string): UnpinnedSource[] {
    const variables = collectVariables(content);
    const found: UnpinnedSource[] = [];
    for (const instruction of groupInstructions(content)) {
        auditClones(instruction, variables, found);
        for (const line of instruction) {
            auditPipGitRefs(line, variables, found);
            auditRawRefs(line, variables, found);
            auditDownloads(line, variables, found);
        }
    }
    return found;
}

/** The `"<kind> <url>"` key a source is recorded under. */
export function pinRecordKey(source: UnpinnedSource): string {
    return `${source.kind} ${source.url}`;
}

/**
 * Fails when the synced Dockerfile's unpinned sources differ from what is recorded, in either
 * direction. Called from the container source sync, so the deployment that would build the image is
 * the one that reports the drift.
 */
export function assertRecordedPinPosture(content: string): void {
    const found = auditDockerfilePins(content);
    const foundKeys = new Set(found.map(pinRecordKey));
    const recorded = new Set(RECORDED_UNPINNED_SOURCES);

    const lineOf = new Map(found.map((source) => [pinRecordKey(source), source.line]));
    const added = [...foundKeys].filter((key) => !recorded.has(key)).sort();
    const nowPinned = [...recorded].filter((key) => !foundKeys.has(key)).sort();

    const problems: string[] = [];
    if (added.length > 0) {
        problems.push(
            `unrecorded unpinned source(s): ${added
                .map((key) => `${key} (line ${lineOf.get(key)})`)
                .join("; ")}`
        );
    }
    if (nowPinned.length > 0) {
        problems.push(`recorded source(s) no longer unpinned: ${nowPinned.join("; ")}`);
    }
    if (problems.length > 0) {
        throw new Error(
            `Splat Toolbox Dockerfile pin posture changed — ${problems.join(" and ")}. ` +
                "Review the source and update RECORDED_UNPINNED_SOURCES in " +
                "constructs/dockerfilePinAudit.ts to match, in the same change."
        );
    }
}
