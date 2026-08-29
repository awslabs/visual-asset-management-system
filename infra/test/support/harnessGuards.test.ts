/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Guards for three rules nothing else in the repository enforces.
 *
 * ## 1. No test may construct a `cdk.App` without an `outdir`
 *
 * A CDK app built with no `outdir` sends its assembly to a fresh temporary directory and nothing
 * removes it, because Jest never fires Node's `exit` event (jestjs/jest#10927). An app built only to
 * read configuration leaves an empty directory; one that reaches `Template.fromStack()` or
 * `app.synth()` leaves the whole assembly, ~280 MB for a full VAMS synth. Every test file goes through
 * `support/testApp.ts` for that reason — but converting the files that existed left nothing stopping
 * the next one, and one support module had already been missed.
 *
 * This is a source-text guard rather than a runtime one on purpose. A runtime hook on the App
 * constructor would also fire for apps constructed inside dependencies (`@aws-cdk/assert`, cdk-nag,
 * a construct library), which cannot be enumerated without running the whole suite, and it would fail
 * in whichever file happened to trigger it rather than in the file that owns the rule.
 *
 * Two things it does NOT catch, and the difference between them matters. An App built through an
 * indirection (`const A = cdk.App; new A()`) is missed, and that miss is visible: nothing is reported,
 * but nothing is wrongly reported either. Source text the comment stripper mis-classifies is the
 * dangerous one, and it can fail in BOTH directions — `new App(` written inside a regex literal is
 * reported as a spurious offender (loud, correctable), while a regex literal is not understood as a
 * literal at all, so its contents are scanned as code.
 *
 * That second direction used to hide offenders rather than invent them. `withoutComments` is a
 * character scanner with no notion of a regex literal, so the escaped-slash pair in a pattern such as
 * `/\/\//` presented it with a bare `//` and it discarded the REST OF THE LINE — including any App
 * construction that followed. It now refuses to open a line comment when the preceding character is a
 * backslash, which outside a string can only be a regex escape (a `\` immediately before `//` is not
 * valid JavaScript anywhere else). The residual mis-classification is therefore the loud direction
 * only, which is what makes the "visible rather than silent" claim above true.
 *
 * Cleanup for anything that slips past is separate: `jest.config.js` registers aws-cdk-lib's own
 * `testhelpers/jest-autoclean` hook in every test file, so a leak that reaches `main` is at least
 * swept at the end of the file that caused it. This guard is what FAILS; that hook only cleans up.
 *
 * ## 2. The SQS event source bounds must be able to fail
 *
 * `sqsEventSourceBounds.ts` is asserted as `expect(batchSizeOffenders(...)).toEqual([])` in two files.
 * An assertion of that shape passes when the checker cannot see anything, so the checker is given a
 * value it MUST reject here.
 *
 * ## 3. No queue comment may claim a receive count is per-message
 *
 * The dead-letter `maxReceiveCount` of the indexer and Physna sync queues was justified in a comment
 * by a claim that only the failed records are redelivered, so the count applies per message. The
 * handlers report the ENTIRE batch on any failure they cannot attribute to one record, and a timeout
 * reports nothing at all, so a receive count advances for every record in the batch. That claim was
 * corrected out of `architecture/aws-resources.md` while both code comments kept it, which is what
 * this guard exists to stop happening again — a false justification standing beside a tuning value is
 * how the value stops being re-examined.
 */

import * as fs from "fs";
import * as path from "path";
import { MAX_SQS_BATCH_SIZE, batchSizeOffenders } from "./sqsEventSourceBounds";

// `require` rather than an import: the jest config is JavaScript and this project compiles with
// `allowJs` off, so an import of it does not type-check.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const jestConfig = require("../../jest.config.js");

/** The test tree this guard covers: every `.ts` file under `infra/test`, this file included. */
const TEST_ROOT = path.resolve(__dirname, "..");

/**
 * The single sanctioned App construction. `newTestApp()` creates the temporary outdir it owns, and
 * honors an explicit one, so its own call is the only place the raw constructor belongs.
 */
const SANCTIONED_FILES = [path.join("support", "testApp.ts")];

/**
 * Split so this file does not match its own detector. The detector looks for the constructor text
 * contiguously; `"new " + "cdk.App"` puts a quote where it expects an identifier.
 */
const APP_CTOR = "new " + "cdk.App";

const tsFilesUnder = (dir: string): string[] =>
    fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return tsFilesUnder(full);
        return entry.isFile() && full.endsWith(".ts") ? [full] : [];
    });

/**
 * Source with comments removed and every newline kept, so reported line numbers still match the file.
 *
 * String literals are preserved (and tracked, so a `//` inside one is not mistaken for a comment),
 * which is what lets a doc comment describe the forbidden construction without tripping the guard.
 */
export function withoutComments(source: string): string {
    let out = "";
    let quote: string | undefined;
    let i = 0;
    while (i < source.length) {
        const ch = source[i];
        const next = source[i + 1];
        if (quote !== undefined) {
            out += ch;
            if (ch === "\\") {
                out += next ?? "";
                i += 2;
                continue;
            }
            if (ch === quote) quote = undefined;
            i++;
            continue;
        }
        if (ch === '"' || ch === "'" || ch === "`") {
            quote = ch;
            out += ch;
            i++;
            continue;
        }
        // A `\` immediately before `//` is a regex escape, never a line comment: outside a string
        // literal there is no valid JavaScript in which a backslash precedes `//`. Without this the
        // escaped-slash pair in a pattern like `/\/\//` reads as a comment start and the rest of the
        // line — an App construction included — is discarded, so the guard passes on an offender.
        if (ch === "/" && next === "/" && !out.endsWith("\\")) {
            while (i < source.length && source[i] !== "\n") i++;
            continue;
        }
        if (ch === "/" && next === "*") {
            i += 2;
            while (i < source.length && !(source[i] === "*" && source[i + 1] === "/")) {
                if (source[i] === "\n") out += "\n";
                i++;
            }
            i += 2;
            continue;
        }
        out += ch;
        i++;
    }
    return out;
}

/** `new App(`, `new cdk.App(`, `new core.App(` — the constructor however it is qualified. */
const APP_CONSTRUCTION = new RegExp("new\\s+(?:[A-Za-z_$][\\w$]*\\s*\\.\\s*)?App\\s*\\(", "g");

/** The text between a construction's parentheses, so it can be checked for an `outdir`. */
function argumentsAt(source: string, openParen: number): string {
    let depth = 0;
    for (let i = openParen; i < source.length; i++) {
        if (source[i] === "(") depth++;
        else if (source[i] === ")") {
            depth--;
            if (depth === 0) return source.slice(openParen + 1, i);
        }
    }
    return source.slice(openParen + 1);
}

/**
 * App constructions in `source` that name no `outdir`, as `line: snippet` strings.
 *
 * Exported so the positive control below can drive it with a source it must reject.
 */
export function unmanagedAppConstructions(source: string): string[] {
    const code = withoutComments(source);
    const offenders: string[] = [];
    for (const match of code.matchAll(APP_CONSTRUCTION)) {
        const openParen = code.indexOf("(", match.index + match[0].length - 1);
        const args = argumentsAt(code, openParen);
        if (/\boutdir\b/.test(args)) continue;
        const line = code.slice(0, match.index).split("\n").length;
        const snippet = `${match[0]}${args})`.replace(/\s+/g, " ").slice(0, 60);
        offenders.push(`${line}: ${snippet}`);
    }
    return offenders;
}

describe("no test constructs a cdk.App without an outdir", () => {
    test("every .ts file under infra/test goes through newTestApp()", () => {
        const files = tsFilesUnder(TEST_ROOT);

        // Control: the walk found the tree. A zero-length list would satisfy the check vacuously,
        // and so would a list missing the support directory this guard itself lives in.
        expect(files.length).toBeGreaterThan(20);
        expect(files.map((f) => path.relative(TEST_ROOT, f))).toContain(
            path.join("support", "testApp.ts")
        );

        const offenders = files.flatMap((file) => {
            const relative = path.relative(TEST_ROOT, file);
            if (SANCTIONED_FILES.includes(relative)) return [];
            return unmanagedAppConstructions(fs.readFileSync(file, "utf8")).map(
                (hit) =>
                    `${relative}:${hit} constructs a CDK app with no outdir; use newTestApp() from ` +
                    `test/support/testApp.ts, or pass an outdir this file removes itself`
            );
        });
        expect(offenders).toEqual([]);
    });

    test("the detector rejects an unmanaged construction and accepts a managed one", () => {
        // Positive control: without this, a detector that matched nothing at all would report the
        // whole tree clean. Both shapes are built at runtime so this file stays clean itself.
        expect(unmanagedAppConstructions(`const app = ${APP_CTOR}();`)).toHaveLength(1);
        expect(
            unmanagedAppConstructions(`const app = ${APP_CTOR}({ context: { a: 1 } });`)
        ).toHaveLength(1);
        expect(unmanagedAppConstructions(`const app = ${APP_CTOR}({ outdir: "/tmp/x" });`)).toEqual(
            []
        );
        // A doc comment about the rule is not an offender, or the rule could not be documented.
        expect(unmanagedAppConstructions(`/** Never write ${APP_CTOR}() here. */`)).toEqual([]);
        expect(unmanagedAppConstructions(`// ${APP_CTOR}()`)).toEqual([]);
    });

    test("an escaped slash in a regex literal does not swallow the rest of the line", () => {
        // The stripper has no notion of a regex literal, so the `\/\/` in this pattern used to read
        // as a comment start and everything after it on the line was discarded — hiding the offender
        // instead of reporting it. Assembled at runtime so this file stays clean itself.
        const escapedSlashes = "const separator = /" + "\\/\\/" + "/;";

        expect(unmanagedAppConstructions(`${escapedSlashes} const app = ${APP_CTOR}();`)).toEqual([
            expect.stringContaining(APP_CTOR),
        ]);
        // Control for the pair above: a real line comment on the same line still strips, so the
        // backslash exception did not simply disable comment handling.
        expect(unmanagedAppConstructions(`${escapedSlashes} // ${APP_CTOR}()`)).toEqual([]);
    });

    test("jest registers the aws-cdk-lib assembly cleanup hook for every test file", () => {
        // The hook is what stops a file that slips past the guard above from leaving its assembly
        // behind. Containment, not equality: further setup files are free to be added.
        expect(jestConfig.setupFilesAfterEnv).toContain("aws-cdk-lib/testhelpers/jest-autoclean");
    });
});

/** The CDK sources whose comments justify a `maxReceiveCount`. */
const LIB_ROOT = path.resolve(__dirname, "..", "..", "lib");

/**
 * Wordings that assert the corrected-out claim: that a redelivery, and so a receive count, is
 * per-record.
 *
 * **This is a fixed list of literal wordings, not a semantic detector.** It covers the two sentences
 * that were removed and their near neighbours — the message/record and message/batch substitutions,
 * and `applies`/`is` for the verb. A materially different rewording of the same claim ("each failing
 * record comes back on its own, so the counter only ever advances for it") matches none of these and
 * passes. Read this guard as a ratchet against the corrected claim being restored more or less as it
 * was, which is the way a deleted justification usually comes back, rather than as proof that no such
 * claim exists anywhere under `infra/lib`.
 *
 * Widening it further is bounded by the opposite risk: the corrective prose itself says "the receive
 * count is not per-message", and a pattern loose enough to catch every affirmative rewording also
 * flags the correction. The negative control below pins that boundary.
 */
const PER_MESSAGE_CLAIMS = [
    /per[- ](?:message|record) rather than per[- ]batch/i,
    /count (?:applies|is) per[- ](?:message|record)/i,
    /only the (?:records|messages) that failed are redelivered/i,
    /only the failed (?:records|messages) are redelivered/i,
];

/** Occurrences of a per-message redelivery claim in `source`, as `line: claim` strings. */
export function perMessageClaims(source: string): string[] {
    return source.split(/\r?\n/).flatMap((line, index) => {
        const claim = PER_MESSAGE_CLAIMS.find((pattern) => pattern.test(line));
        return claim ? [`${index + 1}: ${line.trim()}`] : [];
    });
}

describe("no queue comment claims a dead-letter receive count is per-message", () => {
    test("nothing under infra/lib restates the claim aws-resources.md corrects", () => {
        const files = tsFilesUnder(LIB_ROOT);

        // Controls: the walk found the CDK sources, and the corpus really contains the queues this
        // guard is about — a scan that reached neither would report clean without checking anything.
        expect(files.length).toBeGreaterThan(50);
        const withReceiveCount = files.filter((file) =>
            fs.readFileSync(file, "utf8").includes("maxReceiveCount")
        );
        expect(withReceiveCount.length).toBeGreaterThan(1);

        const offenders = files.flatMap((file) =>
            perMessageClaims(fs.readFileSync(file, "utf8")).map(
                (hit) =>
                    `${path.relative(
                        LIB_ROOT,
                        file
                    )}:${hit} — the handlers report the whole batch ` +
                    `on any failure they cannot attribute to one record, so the receive count is not ` +
                    `per-message`
            )
        );
        expect(offenders).toEqual([]);
    });

    test("the detector recognises each of the wordings it lists", () => {
        // Positive control: a detector matching nothing would call every file clean.
        expect(
            perMessageClaims("// the count applies per message rather than per batch.")
        ).toHaveLength(1);
        expect(
            perMessageClaims("// so only the failed records are redelivered and the rest are gone")
        ).toHaveLength(1);
        // The substitutions the list claims to cover, so the docstring's scope is asserted and not
        // just described: record for message, is for applies, hyphen for space.
        expect(perMessageClaims("// the receive count is per-record, not per-batch")).toHaveLength(
            1
        );
        expect(
            perMessageClaims("// only the messages that failed are redelivered to the consumer")
        ).toHaveLength(1);
        // The boundary the docstring names: the CORRECTION must not be flagged as the claim, or the
        // guard would forbid explaining why the claim is wrong.
        expect(
            perMessageClaims("// the count is not per-message in the failure mode that matters")
        ).toEqual([]);
    });
});

describe("the SQS event source batch bound can fail", () => {
    test("a batch above the bound is reported and everything safer is not", () => {
        const at = "control";
        // The unsafe direction fails...
        expect(
            batchSizeOffenders([{ at, properties: { BatchSize: MAX_SQS_BATCH_SIZE + 1 } }])
        ).toHaveLength(1);
        expect(batchSizeOffenders([{ at, properties: { BatchSize: 5000 } }])).toHaveLength(1);
        // ...a deploy-time value cannot be checked, so it is reported rather than skipped...
        expect(
            batchSizeOffenders([{ at, properties: { BatchSize: { Ref: "BatchSizeParam" } } }])
        ).toHaveLength(1);
        // ...and every strictly safer implementation passes: at the bound, below it, or absent
        // (Lambda's own default for an SQS source is the bound).
        expect(
            batchSizeOffenders([
                { at, properties: { BatchSize: MAX_SQS_BATCH_SIZE } },
                { at, properties: { BatchSize: 1 } },
                { at, properties: {} },
            ])
        ).toEqual([]);
    });
});
