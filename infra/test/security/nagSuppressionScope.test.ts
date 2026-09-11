/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * No CDK Nag suppression may be a catch-all over resource wildcards.
 *
 * Two findings, one shape. `suppressCdkNagErrorsByGrantReadWrite()` (S1-INFRA-071) and the storage
 * nested stack's inline block (S1-INFRA-096) each carried an entry whose `appliesTo` regex matched
 * EVERY `Resource::` finding. 58 of the helper's 76 call sites passed the nested stack rather than the
 * function, and the storage one was applied to a stack owning the KMS key, every DynamoDB table, the
 * asset and auxiliary buckets, the SNS topics and the bucket-sync queues — under a reason that described
 * only the CDK bucket deployment. The repository's primary IAM guardrail was therefore off for most of
 * the Lambda roles in the deployment.
 *
 * Measured against a real `cdk synth` of the commercial configuration, that pair was hiding 148
 * findings. They are now covered by one individually justified entry per wildcard SHAPE that a CDK
 * grant necessarily produces, plus three named opt-in helpers for the specific AWS APIs that publish no
 * resource at all. The remaining count is zero and the synth passes with Nag enabled.
 *
 * Why this suite reads SOURCE rather than a synth: `test/support/templateSynth.ts` sets
 * `enableCdkNag = false`, so no Nag rule runs in this tier and a synth-based assertion here would be
 * vacuous. The property that can be checked cheaply and reliably is the shape of the suppressions
 * themselves — that none of them is a catch-all — and that is what would regress if someone widened one
 * again to make a synth pass.
 */

import * as fs from "fs";
import * as path from "path";

const read = (relative: string) =>
    fs.readFileSync(path.resolve(__dirname, "..", relative), "utf-8");

const LIB_DIR = path.resolve(__dirname, "..", "../lib");

/** Every .ts file under infra/lib, walked recursively. */
function sourceFiles(dir: string = LIB_DIR): string[] {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return sourceFiles(full);
        return entry.isFile() && entry.name.endsWith(".ts") ? [full] : [];
    });
}

/** Every `appliesTo` regex literal in a source file. */
function suppressionRegexes(source: string): string[] {
    return [...source.matchAll(/regex:\s*"([^"]+)"/g)].map((m) => m[1]);
}

/**
 * A regex that matches any `Resource::` finding whatever the resource is.
 *
 * The catch-alls are the two that anchor on `Resource::` and then accept anything to the end; the
 * detector test below pins both spellings as code rather than quoting them here, since a block comment
 * cannot contain a regex ending in the comment terminator. A pattern that merely CONTAINS a wildcard is
 * fine — every shape entry does, because an ARN carries tokens — so this asks whether the regex matches
 * an UNBOUNDED resource, not whether wildcards appear in it at all.
 */
function isCatchAllResourceRegex(regex: string): boolean {
    const body = regex.replace(/^\//, "").replace(/\/[a-z]*$/, "");
    return /^\^?Resource::\.\*\$?$/.test(body);
}

describe("no suppression is a catch-all over Resource wildcards", () => {
    const FILES = [
        "../lib/helper/security.ts",
        "../lib/nestedStacks/storage/storageBuilder-nestedStack.ts",
    ];

    test.each(FILES)("%s declares suppression regexes to check", (file) => {
        // The control. "None is a catch-all" is satisfied by a file with no suppressions at all, and
        // security.ts is where they live — so a refactor that moved them elsewhere must fail here
        // rather than silently stop the check.
        const regexes = suppressionRegexes(read(file));
        if (file.includes("storageBuilder")) {
            // This one now delegates, so it declares none of its own; the delegation is asserted below.
            expect(read(file)).toContain("suppressCdkNagErrorsByGrantReadWrite(this)");
        } else {
            expect(regexes.length).toBeGreaterThan(4);
        }
    });

    test.each(FILES)("%s has no catch-all Resource regex", (file) => {
        const offenders = suppressionRegexes(read(file)).filter(isCatchAllResourceRegex);
        expect(offenders).toEqual([]);
    });

    test("the catch-all detector recognises the pattern that was removed", () => {
        // Without this, `isCatchAllResourceRegex` could be broken and every assertion above would pass
        // against a restored blanket.
        expect(isCatchAllResourceRegex("/^Resource::.*/g")).toBe(true);
        expect(isCatchAllResourceRegex("/^Resource::.*$/g")).toBe(true);
        // And it must NOT flag the shape entries, which legitimately contain `.*` inside a bounded
        // pattern — otherwise the fix could not be expressed at all.
        expect(isCatchAllResourceRegex("/^Resource::<.*Bucket.*\\.Arn>/\\*$/g")).toBe(false);
        expect(isCatchAllResourceRegex("/^Resource::arn:.*:states:.*:\\*$/g")).toBe(false);
        expect(isCatchAllResourceRegex("/^Resource::\\*$/g")).toBe(false);
    });

    test("every shape entry carries its own reason, not a shared one", () => {
        // The original pair reused one `reason` variable across entries, which is how a justification
        // came to describe one construct while the suppression covered a whole stack. Each entry now
        // states why ITS shape is unavoidable.
        const source = read("../lib/helper/security.ts");
        const helper = source.slice(
            source.indexOf("export function suppressCdkNagErrorsByGrantReadWrite"),
            source.indexOf("export function grantExternalAssetBucketKmsKeys")
        );
        expect(helper.length).toBeGreaterThan(0);
        const entries = (helper.match(/id: "AwsSolutions-IAM5"/g) ?? []).length;
        const reasons = (helper.match(/reason:/g) ?? []).length;
        expect(entries).toBeGreaterThanOrEqual(5);
        expect(reasons).toBe(entries);
        // The shared-variable form the finding describes.
        expect(helper).not.toMatch(/reason:\s*reason,/);
    });

    test("an asterisk in a suppression regex is escaped so it matches literally", () => {
        // In a TypeScript double-quoted string `"\*"` is just `*`, because `\*` is not a recognized
        // escape — so the regex cdk-nag receives becomes `::*`, meaning "zero or more colons", and
        // matches nothing. Measured: three suppressions written that way silently applied to nothing
        // and the synth kept failing on findings they were meant to cover.
        for (const file of FILES.concat([
            "../lib/nestedStacks/pipelines/genAi/metadata3dLabeling/lambdaBuilder/metadata3dLabelingFunctions.ts",
        ])) {
            for (const regex of suppressionRegexes(read(file))) {
                // Every `*` intended literally is preceded by a backslash in the resolved string.
                const stray = /(^|[^\\.])\*/.exec(regex.replace(/\.\*/g, ""));
                expect({ file, regex, stray: stray?.[0] ?? null }).toEqual({
                    file,
                    regex,
                    stray: null,
                });
            }
        }
    });

    test("the named opt-in helpers exist for the APIs that publish no resource", () => {
        // These are the three cases a shape regex cannot express, each granted on `*` because the AWS
        // API has no resource to scope to. Named helpers rather than a blanket, so a handler that
        // acquires an unrelated wildcard later still surfaces at synth.
        const source = read("../lib/helper/security.ts");
        expect(source).toContain("export function suppressCdkNagDynamoStreamListWildcard");
        expect(source).toContain("export function suppressCdkNagEcrAuthTokenWildcard");
        expect(source).toContain("dynamodb:ListStreams");
        expect(source).toContain("ecr:GetAuthorizationToken");
    });
});

describe("the per-asset SNS topic grant is pinned to this deployment", () => {
    const SITES = [
        "../lib/lambdaBuilder/assetFunctions.ts",
        "../lib/lambdaBuilder/searchIndexBucketSyncFunctions.ts",
        "../lib/lambdaBuilder/sendEmailFunctions.ts",
        "../lib/lambdaBuilder/subscriptionFunctions.ts",
    ];

    test.each(SITES)("%s builds the AssetTopic ARN", (file) => {
        // Control: the constant must still be built here, or the assertions below hold because the
        // grant moved rather than because it was pinned.
        expect(read(file)).toContain("assetTopicWildcardArn");
    });

    test.each(SITES)("%s does not wildcard the account or Region", (file) => {
        // `arn:<partition>:sns:*:*:AssetTopic*` is a publish grant against any account's topics of that
        // name. Both segments are known at synthesis; only the asset id is not.
        expect(read(file)).not.toContain("sns:*:*:AssetTopic");
    });

    test.each(SITES)("%s pins them to config.env", (file) => {
        const source = read(file);
        const line = source.slice(
            source.indexOf("assetTopicWildcardArn = "),
            source.indexOf("assetTopicWildcardArn = ") + 260
        );
        expect(line).toContain("config.env.region");
        expect(line).toContain("config.env.account");
    });
});

/**
 * No suppression reason may be a bare restatement of the rule (Rule 4).
 *
 * S1-INFRA-122 / S1-INFRA-128 / S1-INFRA-103. Thirty reasons said things like "Intend to use
 * AWSLambdaBasicExecutionRole as is at this stage of this project" or "The IAM role for ECS Container
 * execution uses AWS Managed Policies" — text a reviewer could have read off the finding itself. Rule 4
 * requires the reason to say why the finding is ACCEPTABLE IN VAMS.
 *
 * The banned phrases are listed explicitly rather than inferred from length or wording quality. A
 * heuristic ("shorter than N characters", "does not contain a colon") would fail the wrong files and,
 * worse, would push authors toward padding — and a reason is not free: cdk-nag stamps it onto every
 * resource the suppression covers, so text is multiplied by the resource count against the 1 MB
 * per-template ceiling. Shortness is a virtue here, so only the specific empty phrasings are banned.
 *
 * Measured while making this change: replacing the placeholders with longer justifications moved
 * apiBuilder from 0.48 MB to 0.49 MB and apiBuilder2 from 0.29 to 0.30. Dropping the managed-policy name
 * from the shared reasons — cdk-nag already puts it in the finding id and the `appliesTo` entry — brought
 * both back, so the corrected reasons are SHORTER than the placeholders they replaced.
 */
describe("no CDK Nag reason restates the rule instead of justifying it", () => {
    /** Phrasings that assert nothing about why the finding is acceptable. */
    const BANNED = [
        /as is at this stage/i,
        /as-is at this stage/i,
        /^Suppressed\.?$/i,
        /uses AWS Managed Policies$/i,
        /^The IAM role for ECS Container (execution|job) uses AWS Managed Policies/i,
    ];

    const reasonsByFile = (() => {
        const out: Array<{ file: string; reason: string }> = [];
        for (const file of sourceFiles()) {
            const source = fs.readFileSync(file, "utf-8");
            for (const match of source.matchAll(/reason:\s*"([^"]*)"/g)) {
                out.push({ file: path.relative(LIB_DIR, file), reason: match[1] });
            }
        }
        return out;
    })();

    test("[control] reasons were found to check", () => {
        // An empty list makes the ban below vacuous, and the extraction is a regex over source text.
        expect(reasonsByFile.length).toBeGreaterThan(50);
    });

    test("no reason uses a banned placeholder phrasing", () => {
        const offenders = reasonsByFile
            .filter(({ reason }) => BANNED.some((pattern) => pattern.test(reason)))
            .map(({ file, reason }) => `${file}: "${reason.slice(0, 70)}"`);
        expect(offenders).toEqual([]);
    });

    test("the shared reason constants are the ones actually referenced", () => {
        // The positive control: the ban above is also satisfied by deleting every reason, and by
        // inlining new prose at each site instead of sharing a constant. Sharing is what keeps the
        // emitted text identical and short everywhere.
        const security = fs.readFileSync(path.join(LIB_DIR, "helper", "security.ts"), "utf-8");
        for (const name of [
            "NAG_REASON_LAMBDA_BASIC_EXECUTION",
            "NAG_REASON_LAMBDA_VPC_ACCESS",
            "NAG_REASON_ECS_TASK_EXECUTION_MANAGED",
        ]) {
            expect(security).toContain(`export const ${name} =`);
            // Counted as REFERENCES across the tree, not as files. The two Lambda reasons are applied by
            // suppressCdkNagLambda inside security.ts itself, so no other file imports them — an earlier
            // "used by more than one file" bar failed on exactly that, correctly.
            //
            // Doubled backslashes: inside a template literal `\b` is the BACKSPACE escape (U+0008), so
            // the single-backslash form compiled to a regex matching a control character and found
            // nothing — reporting zero users for a constant used everywhere.
            const references = sourceFiles().reduce((total, file) => {
                const matches = fs
                    .readFileSync(file, "utf-8")
                    .match(new RegExp(`\\b${name}\\b`, "g"));
                return total + (matches ? matches.length : 0);
            }, 0);
            // More than the declaration itself: a constant nobody applies is dead text.
            expect(references).toBeGreaterThan(1);
        }
    });

    test("each shared reason stays to one short sentence", () => {
        // The ceiling constraint, asserted rather than left to reviewer discipline. These three are
        // applied per Lambda across every stack, so their length is multiplied more than any other.
        const security = fs.readFileSync(path.join(LIB_DIR, "helper", "security.ts"), "utf-8");
        for (const name of [
            "NAG_REASON_LAMBDA_BASIC_EXECUTION",
            "NAG_REASON_LAMBDA_VPC_ACCESS",
            "NAG_REASON_ECS_TASK_EXECUTION_MANAGED",
        ]) {
            // `\\s` for the same reason as above: `\s` in a template literal is just the letter s, so
            // the pattern became `=s*"` and matched nothing, leaving the value undefined.
            const value = new RegExp(`export const ${name} =\\s*"([^"]*)"`).exec(security)?.[1];
            expect(value).toBeDefined();
            expect(value!.length).toBeLessThanOrEqual(110);
            // One sentence: no interior period followed by a capital.
            expect(value!).not.toMatch(/\.\s+[A-Z]/);
        }
    });
});

/**
 * Every `appliesTo` regex must be wrapped in slashes, or cdk-nag compiles a different pattern.
 *
 * S1-INFRA-102. Forty-four entries were written with no leading slash — the pattern text, then a
 * trailing flag suffix, but no opening delimiter. cdk-nag parses a regex entry by matching the first
 * slash, then a greedy run, then the LAST slash. With no leading delimiter the first slash it finds is
 * one INSIDE the pattern, so the entry compiled to a pattern anchored on ServiceRole alone — measured
 * in the test below — which matches any construct's ServiceRole path, including other pipelines'. The
 * entries were therefore not inert but far BROADER than written, and the intended scoping never applied.
 *
 * Correctly wrapped, the same patterns match nothing, because real finding ids carry a CDK-generated
 * suffix (`openPipeline1A2B3C/ServiceRole/`) between the construct name and the path. That raised the
 * question of whether they were load-bearing, so it was measured rather than assumed: a full
 * `cdk synth --all` with CDK Nag enabled, against a `config.json` that enables ten of the eleven affected
 * pipelines, exited 0 and emitted 155 templates with one pre-existing COG2 *warning* and no errors. The
 * findings are covered by `suppressCdkNagLambda()` and the explicit path suppressions, exactly as the
 * finding predicted, so wrapping them removes over-broad coverage and loses nothing.
 *
 * They are left in place rather than deleted: correctly formed and matching nothing is a safe state, and
 * deleting 44 call sites across 11 files is churn with no behavioural difference — nag sees no suppression
 * either way. This guard is what keeps the malformed form from coming back.
 */
describe("every appliesTo regex is wrapped in slashes", () => {
    /** cdk-nag's own parse, from node_modules/cdk-nag/lib/utils/nag-suppression-helper.js. */
    const cdkNagToRegEx = (s: string): RegExp => {
        const m = s.match(/\/(.*)\/(.*)?/);
        if (!m) throw new Error("cdk-nag cannot parse this regex entry");
        return new RegExp(m[1], m[2] || "");
    };

    const entries = (() => {
        const out: Array<{ file: string; value: string }> = [];
        for (const file of sourceFiles()) {
            const source = fs.readFileSync(file, "utf-8");
            for (const match of source.matchAll(/regex:\s*"([^"]*)"/g)) {
                out.push({ file: path.relative(LIB_DIR, file), value: match[1] });
            }
        }
        return out;
    })();

    test("[control] regex entries were found to check", () => {
        // An empty list makes the assertions below vacuous.
        expect(entries.length).toBeGreaterThan(40);
    });

    test("no entry is missing its leading slash", () => {
        const offenders = entries
            .filter(({ value }) => !value.startsWith("/"))
            .map(({ file, value }) => `${file}: "${value}"`);
        expect(offenders).toEqual([]);
    });

    test("every entry parses, and its flag suffix is a real flag string", () => {
        // Deliberately NOT comparing the compiled source against the entry text. This test reads FILE
        // TEXT, where escape sequences are still literal (`\\/` on disk is `\/` at runtime), while
        // `RegExp.source` is post-resolution and additionally escapes a bare slash — so the two differ on
        // every pattern containing a slash even when they describe the same thing. An earlier draft
        // compared them and failed on a correct entry.
        //
        // What is checked instead: the entry parses at all, and the text cdk-nag takes as FLAGS is a real
        // flag string. A missing leading slash usually shows up as a nonsense flag ("g" happens to be
        // valid, which is why the leading-slash test above is the primary guard) but a stray slash
        // elsewhere surfaces here.
        const VALID_FLAGS = /^[dgimsuvy]*$/;
        for (const { file, value } of entries) {
            expect(() => cdkNagToRegEx(value)).not.toThrow();
            const flags = value.slice(value.lastIndexOf("/") + 1);
            expect({ file, value, flags, valid: VALID_FLAGS.test(flags) }).toEqual({
                file,
                value,
                flags,
                valid: true,
            });
        }
    });

    test("the malformed form really did compile to something broader", () => {
        // The measurement this guard rests on, kept as a test so the claim is not restated from memory.
        const malformed = "^Resource::.*openPipeline/ServiceRole/.*/g";
        // Two backslashes: `RegExp.source` escapes the bare slash, and in a TypeScript string a single
        // `\/` is just `/` — the same single-backslash trap that made two earlier assertions in this
        // file compare against a pattern they only appeared to state.
        expect(cdkNagToRegEx(malformed).source).toBe("ServiceRole\\/.*");
        // The point of the measurement: the whole `^Resource::.*openPipeline` prefix is GONE.
        expect(cdkNagToRegEx(malformed).source).not.toContain("openPipeline");
        // And it matched a DIFFERENT construct's finding, which is the actual harm.
        expect(
            new RegExp(cdkNagToRegEx(malformed).source).test("Resource::<Unrelated/ServiceRole/X>")
        ).toBe(true);
    });
});

/**
 * No pipeline nested stack may suppress Nag findings at STACK level.
 *
 * S1-INFRA-091. The RapidPipelineEKS nested stack carried one `addStackSuppressions` call covering IAM4,
 * IAM5, SF1, SF2, EKS2 and L1 across its whole subtree with `applyToChildren`, so it also covered every
 * resource added there in future — the same blanket shape this file's first section removed from the
 * shared grant helper, expressed at stack level instead.
 *
 * The replacement was authored from measurement rather than guesswork. With the blanket removed, a full
 * `cdk synth --all` with Nag enabled reported exactly 19 findings (9 IAM4, 5 EKS2, 4 IAM5, 1 SF1, 1 SF2)
 * with their resource paths; each is now covered by a path-scoped entry naming the specific managed
 * policy, log export or resource. The stack-level L1 entry was dropped as redundant: the file already
 * carries path-scoped L1 entries, so its removal changed nothing — which is a weaker claim than L1
 * never firing, and the weaker claim is the one the measurement supports. A second synth with the scoped entries in place exited 0 with 150 templates emitted and only the
 * pre-existing Cognito MFA warning, which is what confirms the coverage is exact rather than approximate.
 *
 * `security.ts` keeps two legitimate stack-level calls for CDK framework resources that cannot be reached
 * by path, so the ban is scoped to the pipelines tree.
 */
describe("no pipeline stack suppresses findings at stack level", () => {
    const PIPELINES_DIR = path.join(LIB_DIR, "nestedStacks", "pipelines");

    const pipelineFiles = sourceFiles(PIPELINES_DIR);

    test("[control] the pipelines tree was walked", () => {
        expect(pipelineFiles.length).toBeGreaterThan(20);
    });

    test("no file under lib/nestedStacks/pipelines calls addStackSuppressions", () => {
        const offenders = pipelineFiles
            .filter((file) => {
                // Comment lines stripped: the EKS stack explains the removal in prose that names the
                // method, and a raw substring search reports that explanation as a live call.
                const code = fs
                    .readFileSync(file, "utf-8")
                    .split("\n")
                    .filter((line) => !line.trim().startsWith("//") && !line.trim().startsWith("*"))
                    .join("\n");
                return code.includes("addStackSuppressions");
            })
            .map((file) => path.relative(LIB_DIR, file));
        expect(offenders).toEqual([]);
    });

    test("the EKS stack still suppresses by PATH, so the findings stay covered", () => {
        // The positive control. Without it, deleting every suppression from that stack would satisfy the
        // ban above while breaking synthesis for any deployment that enables the EKS pipeline.
        const eksStack = fs.readFileSync(
            path.join(
                PIPELINES_DIR,
                "multi",
                "rapidPipelineEKS",
                "rapidPipelineEKS-nestedStack.ts"
            ),
            "utf-8"
        );
        expect(eksStack).toContain("addResourceSuppressionsByPath");
        // The rules the measured run produced, each now named explicitly.
        for (const rule of [
            "AwsSolutions-IAM4",
            "AwsSolutions-IAM5",
            "AwsSolutions-SF1",
            "AwsSolutions-SF2",
            "AwsSolutions-EKS2",
        ]) {
            expect(eksStack).toContain(rule);
        }
        // Nothing is asserted about AwsSolutions-L1 here, and the reason is worth stating: the file
        // carries PRE-EXISTING path-scoped L1 entries which were still in place during the measurement
        // run, so "no L1 finding appeared" does not establish that L1 never fires — only that the
        // stack-level L1 entry added nothing those path entries did not already cover. An earlier draft
        // asserted L1 was absent and failed on exactly those pre-existing entries.
    });
});
