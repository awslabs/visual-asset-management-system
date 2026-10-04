/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Guards FIX-058 (S7-DOCS-008): documented Cosmos 3 Nano/Super input-file modes that the shipped
 * schema's inputFileArity "none" rejects.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import { INDEX_HTML_INLINE_SCRIPT_HASHES } from "../../lib/helper/cspInlineScriptHashes";
import {
    RESTRICTED_TEMPLATES,
    SynthResult,
    synthTemplate,
    TemplateName,
} from "../support/templateSynth";
import { newTestApp } from "../support/testApp";

// Full-app synth from a config template costs ~20 s and the harness caches one result per template.
jest.setTimeout(600_000);

/**
 * Key on `globalThis` holding the `config.json` contents `getConfig()` should see, or `undefined` for the
 * real file. It lives on `globalThis` rather than in a module-level binding because the `jest.mock("fs")`
 * factory below is hoisted above this module's own initialization, and `aws-cdk-lib` reads files while
 * being imported — a `let` would still be in its temporal dead zone at that point.
 */
const CONFIG_OVERRIDE_KEY = "__vamsDocConsistencyConfigJson";

/**
 * `getConfig()` reads `config/config.json` from disk, and the tests below need it to read a chosen
 * template instead. `jest.spyOn(fs, "readFileSync")` cannot do this — Node defines the `fs` exports as
 * non-configurable, so the spy fails with "Cannot redefine property".
 *
 * The replacement is a plain function rather than a `jest.fn`: this module also synthesizes the whole app,
 * during which CDK reads thousands of files, and a mock would retain every path AND every returned Buffer
 * in `mock.results`. It also means there is no mock to reset — a `mockReset()` in an `afterEach` would
 * leave `readFileSync` returning `undefined` for the synth tests that follow.
 *
 * Only `config.json` is intercepted. The S3 and WAF policy JSON that `getConfig()` also loads falls
 * through: their names end in `Config.json`, which is a case-sensitive miss.
 */
jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return {
        ...actual,
        readFileSync: (p: unknown, ...rest: unknown[]) => {
            const override = (globalThis as any).__vamsDocConsistencyConfigJson;
            if (override !== undefined && typeof p === "string" && p.endsWith("config.json")) {
                return override;
            }
            return (actual.readFileSync as any)(p, ...rest);
        },
    };
});

/**
 * Guards documentation statements that assert a specific value produced by code.
 *
 * This exists because of a repeated failure: a behaviour change lands, its code and tests are updated, and
 * the pages describing the old behaviour are left alone. It happened twice in one review — log retention
 * was aligned across 17 source files while ten documentation statements still claimed ten years, and the
 * Content Security Policy moved to per-script hashes while `architecture/security.md` still listed
 * `'unsafe-inline'`. Neither was caught by a test, because no test read the documentation.
 *
 * Scope is deliberately narrow: only values where documentation makes a checkable claim about code output.
 * It is not a prose or link checker. The broader documentation-consistency family (route registry vs
 * OpenAPI vs Docusaurus, three-way resource-name keys) is separate work — see the test plan.
 *
 * When one of these fails, the fix is usually the documentation, not the assertion.
 */

const DOCS = path.join(__dirname, "..", "..", "..", "documentation", "docusaurus-site", "docs");

const read = (rel: string): string => fs.readFileSync(path.join(DOCS, rel), "utf8");

/** A source file under `infra/`, for claims the documentation makes about infrastructure code. */
const readInfraSource = (rel: string): string =>
    fs.readFileSync(path.join(__dirname, "..", "..", rel), "utf8");

/**
 * The body of a Docusaurus admonition, addressed by its title line.
 *
 * Titles are structural — a copy-edit rewords prose but keeps the title — so the caveat assertions below
 * anchor on the title and then check the body for the specific facts, rather than matching a sentence.
 */
const admonitionBody = (text: string, title: string): string => {
    const start = text.indexOf(title);
    if (start < 0) return "";
    const bodyStart = start + title.length;
    const end = text.indexOf("\n:::", bodyStart);
    return end < 0 ? "" : text.slice(bodyStart, end);
};

const PARTITION_CAVEAT_TITLE = ":::warning[Availability outside the commercial partition]";
const TARGET_CAVEAT_TITLE =
    ":::warning[Execution targets are shape-validated only, and pipeline authoring is " +
    "administrator-equivalent]";
const DEPLOY_WINDOW_CAVEAT_TITLE =
    ":::warning[Wait for every stack to complete before using the deployment]";

/** Enable one model variant per NVIDIA pipeline, which is the minimum getConfig() accepts. */
const enableNvidiaPipelines = (c: any) => {
    c.app.pipelines.useNvidiaCosmos.enabled = true;
    c.app.pipelines.useNvidiaCosmos.huggingFaceToken = "/vams/test/hf-token";
    c.app.pipelines.useNvidiaCosmos.modelsPredict.text2world2B_v2.enabled = true;
    c.app.pipelines.useNvidiaCosmos3.enabled = true;
    c.app.pipelines.useNvidiaCosmos3.huggingFaceToken = "/vams/test/hf-token";
    c.app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.enabled = true;
    c.app.pipelines.useNvidiaGr00t.enabled = true;
    c.app.pipelines.useNvidiaGr00t.huggingFaceToken = "/vams/test/hf-token";
    c.app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.enabled = true;
};

/**
 * The one synth per restricted template that every synth assertion in this file shares.
 *
 * `synthTemplate` caches per (template, mutation key), so naming the same key everywhere keeps the cost
 * at one full-app synth per template. The NVIDIA pipelines are enabled in it because the workflow-role
 * assertions do not depend on them and the pipeline-stack assertion does.
 */
const synthWithNvidia = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enableNvidiaPipelines, mutateKey: "nvidia-genai-enabled" });

describe("documented values match the code that produces them", () => {
    describe("Content Security Policy — architecture/security.md", () => {
        const securityMd = read("architecture/security.md");

        test("the page exists and documents a script-src directive", () => {
            // Positive control: if the section is renamed or removed, the assertions below would pass
            // vacuously against an empty match.
            expect(securityMd).toContain("Content Security Policy");
            expect(securityMd).toMatch(/`script-src`/);
        });

        test("the base script-src row does not claim 'unsafe-inline'", () => {
            // 'unsafe-inline' is conditional on the Physna add-on. A base-table claim would tell a reader
            // that inline script is permitted unconditionally, which is wrong for a default deployment —
            // and would also imply the SHA-256 hashes are inert, since a policy cannot use both.
            const baseRow = securityMd
                .split("\n")
                .find((l) => l.includes("`script-src`") && l.includes("|"));
            expect(baseRow).toBeDefined();
            expect(baseRow).not.toContain("'unsafe-inline'");
        });

        test("the base script-src row states that hashes are used", () => {
            const baseRow = securityMd
                .split("\n")
                .find((l) => l.includes("`script-src`") && l.includes("|"));
            expect(baseRow).toMatch(/SHA-256|hash/i);
        });

        test("the conditional table records that the Physna add-on adds 'unsafe-inline'", () => {
            // The one configuration that relaxes inline-script protection must be discoverable from the
            // page that documents the policy.
            const physnaLines = securityMd
                .split("\n")
                .filter((l) => /physna/i.test(l) && l.includes("|"));
            expect(physnaLines.length).toBeGreaterThan(0);
            expect(physnaLines.some((l) => l.includes("'unsafe-inline'"))).toBe(true);
        });

        test("the hash count in code is plausible for the documented wording", () => {
            // Ties the prose ("per-script SHA-256 hashes", plural) to the generated constant, so removing
            // all but one hash would surface here.
            expect(INDEX_HTML_INLINE_SCRIPT_HASHES.length).toBeGreaterThan(1);
        });
    });

    describe("Log retention", () => {
        // The aspect applies ONE_YEAR. Any page stating a different period is wrong, and a ten-year claim
        // specifically is the one that shipped for a whole release.
        const pages = [
            "architecture/networking.md",
            "architecture/security.md",
            "developer/audit-logging.md",
            "architecture/aws-resources.md",
        ];

        test.each(pages)("%s does not claim ten-year retention", (rel) => {
            const text = read(rel);
            const offending = text
                .split("\n")
                .map((line, i) => ({ line, n: i + 1 }))
                .filter(({ line }) => /retention|retain/i.test(line))
                .filter(({ line }) => /\b(10|ten)[- ]year|3,?653\b/i.test(line))
                // The instruction for extending retention names TEN_YEARS as the value to pass; that is
                // guidance about how to change it, not a claim about what ships.
                .filter(({ line }) => !/`TEN_YEARS`/.test(line));

            expect(offending.map((o) => `${rel}:${o.n} ${o.line.trim()}`)).toEqual([]);
        });

        test("at least one page states the one-year period", () => {
            // Positive control: proves the pages actually discuss retention, so the negative assertions
            // above are meaningful rather than passing because the topic is absent.
            const stated = pages.some((rel) => /\b(1|one)[- ]year\b/i.test(read(rel)));
            expect(stated).toBe(true);
        });

        test("audit-logging.md points at the aspect rather than a construct property", () => {
            // The page previously told readers to edit a `retention` property that the aspect overwrites.
            const text = read("developer/audit-logging.md");
            expect(text).toContain("LogRetentionAspect");
        });
    });

    describe("External S3 buckets — deployment/external-s3-setup.md", () => {
        const extMd = read("deployment/external-s3-setup.md");

        test("does not claim VAMS overwrites the bucket's notification configuration", () => {
            // CDK merges for an imported bucket. The overwrite claim deterred adoption and prescribed
            // unnecessary migration work.
            const offending = extMd
                .split("\n")
                .filter((l) => /notification/i.test(l))
                .filter((l) => /overwrit|replaces the bucket|are removed/i.test(l));
            expect(offending).toEqual([]);
        });

        test("states that the bucket must be in the deployment Region", () => {
            // getConfig() now rejects a mismatch, so the page must not describe it as merely recommended.
            expect(extMd).toMatch(
                /same AWS Region as the deployment|must equal the VAMS deployment Region/i
            );
        });

        test("does not describe the external KMS grant as manual-only", () => {
            const offending = extMd
                .split("\n")
                .filter((l) => /kms/i.test(l))
                .filter((l) => /not granted to VAMS roles automatically/i.test(l));
            expect(offending).toEqual([]);
        });

        /** Values Amazon S3 accepts in a CORS rule's `AllowedMethods`. */
        const S3_CORS_METHODS = ["GET", "PUT", "POST", "DELETE", "HEAD"];
        const CORS_STEP_HEADING = "### Step 2: Configure CORS";
        const RESERVED_FOLDERS_TITLE = ":::warning[Reserved folder names]";

        interface CorsRule {
            AllowedMethods: string[];
            ExposeHeaders: string[];
        }

        type CorsConfiguration = CorsRule[] | { CORSRules?: CorsRule[] };

        /** The first fenced `json` block under the CORS step, parsed. */
        const documentedCorsConfiguration = (): CorsConfiguration | undefined => {
            const heading = extMd.indexOf(CORS_STEP_HEADING);
            const fence =
                heading < 0 ? null : /```json\r?\n([\s\S]*?)\r?\n```/.exec(extMd.slice(heading));
            return fence ? JSON.parse(fence[1]) : undefined;
        };

        /** The first rule of the documented configuration, in rule-array or `CORSRules` form. */
        const documentedCorsRule = (): CorsRule => {
            const configuration = documentedCorsConfiguration();
            const rules = Array.isArray(configuration) ? configuration : configuration?.CORSRules;
            return rules?.[0] ?? { AllowedMethods: [], ExposeHeaders: [] };
        };

        test("the CORS sample was parsed", () => {
            // Positive control: a renamed heading or a re-fenced sample would leave the rule empty,
            // and the subset assertion below passes on an empty list.
            expect(documentedCorsRule().AllowedMethods.length).toBeGreaterThan(0);
            expect(documentedCorsRule().ExposeHeaders).toContain("ETag");
        });

        test("the CORS sample lists only methods Amazon S3 accepts", () => {
            // Amazon S3 answers the OPTIONS preflight from the rule; OPTIONS is not a list value.
            const unsupported = documentedCorsRule().AllowedMethods.filter(
                (m) => !S3_CORS_METHODS.includes(m)
            );
            expect(unsupported).toEqual([]);
        });

        test("the CORS sample is the CORSRules document that put-bucket-cors takes", () => {
            // --cors-configuration takes {"CORSRules": [...]}. A bare rule array is the format of
            // the Amazon S3 console's CORS editor, and the AWS CLI rejects it before any request.
            const configuration = documentedCorsConfiguration();
            expect(Array.isArray(configuration)).toBe(false);
            const rules = Array.isArray(configuration) ? undefined : configuration?.CORSRules;
            expect(rules?.length ?? 0).toBeGreaterThan(0);
        });

        test("the CORS sample exposes the range-read headers", () => {
            expect(documentedCorsRule().ExposeHeaders).toEqual(
                expect.arrayContaining(["Accept-Ranges", "Content-Range"])
            );
        });

        test("the reserved-folder warning scopes the check to every folder in the key", () => {
            // key_has_reserved_segment tests every segment of the raw key and of the
            // prefix-stripped remainder by exact, case-sensitive membership, so the prefix's own
            // folders and the file name itself count too.
            const body = admonitionBody(extMd, RESERVED_FOLDERS_TITLE);
            expect(body.length).toBeGreaterThan(0);
            expect(body).not.toMatch(/top-level/i);
            expect(body).toMatch(/any folder in its key/);
            expect(body).toMatch(/subfolder inside an asset folder/);
            expect(body).toMatch(/`baseAssetsPrefix` itself/);
            expect(body).toMatch(/file whose whole name is one of these names/);
            expect(body).toMatch(/case-sensitive/);
        });

        const IAM_STEP_HEADING = "### Step 4: Configure cross-account IAM (conditional)";
        const MANUAL_KMS_POLICY_INTRO = "The field takes one key";
        const DEPLOY_INFO_TITLE = ":::info[What happens during deployment]";
        const AUTO_LIST_START = "VAMS configures automatically (from Account A)";
        const OWNER_LIST_START = "The bucket owner must configure manually (in Account B)";
        const STEP_4_ANCHOR = "#step-4-configure-cross-account-iam-conditional";

        /** The actions in the statement `grantExternalAssetBucketKmsKeys` attaches to a role. */
        const externalKeyGrantActions = (): string[] => {
            const source = readInfraSource(path.join("lib", "helper", "security.ts"));
            const start = source.indexOf("export function grantExternalAssetBucketKmsKeys");
            if (start < 0) return [];
            const end = source.indexOf("\nexport function ", start + 1);
            const body = source.slice(start, end < 0 ? undefined : end);
            const actions = /actions:\s*\[([^\]]*)\]/.exec(body);
            return actions ? [...actions[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : [];
        };

        /** The backticked `kms:` actions named in one line of the page. */
        const kmsActionsIn = (line: string): string[] =>
            [...line.matchAll(/`(kms:[A-Za-z*]+)`/g)].map((m) => m[1]);

        const asSortedSet = (values: string[]): string[] => [...new Set(values)].sort();

        /** The text between two markers, or "" when either marker is missing. */
        const textBetween = (text: string, from: string, to: string): string => {
            const start = text.indexOf(from);
            const end = start < 0 ? -1 : text.indexOf(to, start + from.length);
            return end < 0 ? "" : text.slice(start + from.length, end);
        };

        const numberedLines = (): Array<{ line: string; n: number }> =>
            extMd.split("\n").map((line, i) => ({ line, n: i + 1 }));

        test("the external-key grant actions were read from security.ts", () => {
            // Positive control: a renamed helper or a reshaped statement leaves the list empty, and
            // the set comparisons below would then compare nothing.
            const actions = externalKeyGrantActions();
            expect(actions).toContain("kms:Decrypt");
            expect(actions.length).toBeGreaterThan(1);
        });

        test("every statement of the external-key grant names the actions the code grants", () => {
            const expected = asSortedSet(externalKeyGrantActions()).join(", ");
            const claims = numberedLines()
                .filter(({ line }) => line.includes("`bucketKmsKeyArn`"))
                .filter(({ line }) => kmsActionsIn(line).length > 0);
            // Positive control: the page states the granted actions in more than one place.
            expect(claims.length).toBeGreaterThanOrEqual(2);
            const wrong = claims
                .map(({ line, n }) => ({ n, named: asSortedSet(kmsActionsIn(line)).join(", ") }))
                .filter(({ named }) => named !== expected)
                .map(({ n, named }) => `external-s3-setup.md:${n} ${named}`);
            expect(wrong).toEqual([]);
        });

        test("the manual external-key policy grants the same actions as the automatic grant", () => {
            const step4 = extMd.indexOf(IAM_STEP_HEADING);
            const intro = step4 < 0 ? -1 : extMd.indexOf(MANUAL_KMS_POLICY_INTRO, step4);
            expect(intro).toBeGreaterThan(0);
            const fence = /```json\r?\n([\s\S]*?)\r?\n```/.exec(extMd.slice(intro));
            expect(fence).not.toBeNull();
            const policy = JSON.parse(fence ? fence[1] : "{}");
            const actions: string[] = policy.Statement?.[0]?.Action ?? [];
            expect(asSortedSet(actions)).toEqual(asSortedSet(externalKeyGrantActions()));
        });

        test("the deployment summary states the conditional external-key grant", () => {
            // grantExternalAssetBucketKmsKeys grants the key to the VAMS roles whenever the entry
            // sets bucketKmsKeyArn; only the Account B key policy is the bucket owner's to apply.
            const body = admonitionBody(extMd, DEPLOY_INFO_TITLE);
            expect(body.length).toBeGreaterThan(0);
            expect(body).not.toMatch(/external KMS grants/i);
            expect(body).toContain("`bucketKmsKeyArn`");
            expect(body).toMatch(/key policy/);
        });

        test("the bucket owner's list leaves the IAM grant on the VAMS roles to Account A", () => {
            const autoList = textBetween(extMd, AUTO_LIST_START, OWNER_LIST_START);
            const ownerList = textBetween(extMd, OWNER_LIST_START, ":::note");
            // Positive control: both lists were found.
            expect(autoList).toMatch(/Lambda and pipeline permissions/);
            expect(ownerList).toMatch(/KMS key access/);
            expect(ownerList).not.toMatch(/VAMS roles/);
            expect(ownerList).not.toContain(STEP_4_ANCHOR);
            expect(autoList).toContain("`bucketKmsKeyArn`");
        });

        test("bucketKmsKeyArn is documented as required for a customer managed key on both pages", () => {
            const refMd = read("deployment/configuration-reference.md");
            const row = (md: string) =>
                md.split("\n").find((l) => l.startsWith("| `bucketKmsKeyArn`")) ?? "";
            expect(row(extMd)).toContain("Required for a customer managed key");
            expect(row(refMd)).toContain(
                "Required if the bucket uses SSE-KMS with a customer managed key"
            );
            expect(extMd).not.toMatch(/prefer not to set `bucketKmsKeyArn`/);
        });

        const VAMS_KEY_STEP_HEADING = "#### 3b. VAMS-owned CMK in Account A";
        const S3_NOTIFICATION_SID = 'sid: "AllowExternalBucketS3Notifications"';

        /** The actions of the key policy statement the storage stack adds for external accounts. */
        const s3NotificationStatementActions = (): string[] => {
            const source = readInfraSource(
                path.join("lib", "nestedStacks", "storage", "storageBuilder-nestedStack.ts")
            );
            const start = source.indexOf(S3_NOTIFICATION_SID);
            if (start < 0) return [];
            const end = source.indexOf("})", start);
            const statement = source.slice(start, end < 0 ? undefined : end);
            const actions = /actions:\s*\[([^\]]*)\]/.exec(statement);
            return actions ? [...actions[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : [];
        };

        /** Step 3b, from its heading to the next heading of level 2 to 4. */
        const vamsKeyStep = (): string => {
            const start = extMd.indexOf(VAMS_KEY_STEP_HEADING);
            if (start < 0) return "";
            const rest = extMd.slice(start + VAMS_KEY_STEP_HEADING.length);
            const next = rest.search(/\n#{2,4} /);
            return next < 0 ? rest : rest.slice(0, next);
        };

        /** The `Action` of the first JSON sample in Step 3b. */
        const vamsKeyStepSampleActions = (): string[] => {
            const fence = /```json\r?\n([\s\S]*?)\r?\n```/.exec(vamsKeyStep());
            const action = fence ? JSON.parse(fence[1]).Action : undefined;
            if (action === undefined) return [];
            return Array.isArray(action) ? action : [action];
        };

        const sortedUnique = (values: string[]): string[] => [...new Set(values)].sort();

        test("the VAMS key's S3 notification statement was read from the storage stack", () => {
            // Positive control: a renamed Sid or a reshaped statement leaves the list empty, and a
            // missing sample would then compare equal to it.
            const actions = s3NotificationStatementActions();
            expect(actions).toContain("kms:Decrypt");
            expect(actions.length).toBeGreaterThan(1);
        });

        test("the Step 3b sample statement names the actions the storage stack grants", () => {
            // VAMS adds this statement to a key it generated, and the sample is what an operator adds
            // to an imported key, so the two grant the same actions.
            expect(sortedUnique(vamsKeyStepSampleActions())).toEqual(
                sortedUnique(s3NotificationStatementActions())
            );
        });

        test("Step 3b states the key policy for a generated and for an imported key", () => {
            // The storage stack adds the statement only to a key it generated, scoped to each entry's
            // bucketAccountId; the policy of a key imported through optionalExternalCmkArn is the
            // operator's to change.
            const step = vamsKeyStep();
            expect(step.length).toBeGreaterThan(0);
            expect(step).toMatch(/\boptionalExternalCmkArn\b/);
            expect(step).toMatch(/\bbucketAccountId\b/);
        });
    });

    describe("Restricted-partition caveat — NVIDIA GenAI pipelines", () => {
        // getConfig() validates the SHAPE of an enabled model variant (a non-empty instanceTypes array)
        // and nothing about availability, so the pages that tell an operator to enable these pipelines
        // are the only place the partition caveat can live.
        const pages: Array<[string, string]> = [
            ["pipelines/nvidia-cosmos-3.md", "# NVIDIA Cosmos 3 Pipeline"],
            ["pipelines/nvidia-cosmos-predict.md", "# NVIDIA Cosmos Predict Pipeline"],
            ["pipelines/nvidia-gr00t-finetune.md", "# NVIDIA Gr00t Fine-Tuning Pipeline"],
            ["deployment/configuration-reference.md", "## Processing pipelines (`app.pipelines`)"],
        ];

        test.each(pages)("%s was read and still carries its known heading", (rel, heading) => {
            // Positive control: an empty or renamed page would satisfy every assertion below by
            // matching nothing.
            const text = read(rel);
            expect(text.length).toBeGreaterThan(0);
            expect(text).toContain(heading);
        });

        test.each(pages)("%s carries the partition caveat", (rel) => {
            expect(read(rel)).toContain(PARTITION_CAVEAT_TITLE);
        });

        test.each(pages)("%s names the fact that only the array shape is checked", (rel) => {
            const body = admonitionBody(read(rel), PARTITION_CAVEAT_TITLE);
            expect(body).toContain("`instanceTypes`");
            expect(body).toMatch(/non-empty/);
        });

        test.each(pages)("%s names both restricted partitions and the action to take", (rel) => {
            const body = admonitionBody(read(rel), PARTITION_CAVEAT_TITLE);
            expect(body).toMatch(/GovCloud/);
            expect(body).toMatch(/European Sovereign/);
            expect(body).toMatch(/evaluate/i);
        });
    });

    describe("Execution-target caveat — pipeline authoring reach", () => {
        const pages: Array<[string, string]> = [
            ["pipelines/custom-pipelines.md", "## Pipeline execution types"],
            ["concepts/pipelines-and-workflows.md", "### Pipeline execution types"],
        ];

        test.each(pages)("%s was read and still carries its known heading", (rel, heading) => {
            const text = read(rel);
            expect(text.length).toBeGreaterThan(0);
            expect(text).toContain(heading);
        });

        test.each(pages)("%s carries the execution-target caveat", (rel) => {
            expect(read(rel)).toContain(TARGET_CAVEAT_TITLE);
        });

        test.each(pages)(
            "%s names the IAM scope rather than implying account-wide reach",
            (rel) => {
                // "Unvalidated by design" reads as "anything in the account", which overstates the reach in
                // the direction that misleads an operator. The pages name the actual wildcard instead.
                const body = admonitionBody(read(rel), TARGET_CAVEAT_TITLE);
                expect(body).toContain("`lambda:InvokeFunction`");
                expect(body).toContain("`sqs:SendMessage`");
                expect(body).toContain("`events:PutEvents`");
                expect(body).toMatch(/`name` configuration value \(default `vams`\)/);
                expect(body).toMatch(/`vams-\*`/);
                expect(body).toMatch(/`default` bus/);
                expect(body).toMatch(/own account and Region/);
            }
        );

        test.each(pages)("%s states that authoring is administrator-equivalent", (rel) => {
            const body = admonitionBody(read(rel), TARGET_CAVEAT_TITLE);
            expect(body).toMatch(/administrator-equivalent/);
        });
    });

    describe("Deploy-window caveat — every stack must complete", () => {
        const pages: Array<[string, string]> = [
            ["developer/setup.md", "## Infrastructure Setup"],
            ["deployment/deploy-the-solution.md", "## Step 8: Deploy"],
        ];

        test.each(pages)("%s was read and still carries its known heading", (rel, heading) => {
            const text = read(rel);
            expect(text.length).toBeGreaterThan(0);
            expect(text).toContain(heading);
        });

        test.each(pages)("%s carries the deploy-window caveat", (rel) => {
            expect(read(rel)).toContain(DEPLOY_WINDOW_CAVEAT_TITLE);
        });

        test.each(pages)("%s names the mechanism and the observable consequence", (rel) => {
            const body = admonitionBody(read(rel), DEPLOY_WINDOW_CAVEAT_TITLE);
            expect(body).toContain("`ResourceNamesBuilder`");
            expect(body).toMatch(/AWS Systems Manager Parameter Store/);
            expect(body).toMatch(/bucket-sync/);
            // The consequence is a retried invocation, not a failed deployment and not a lost event.
            expect(body).toMatch(/retried/);
        });
    });
});

/**
 * The negative control for the partition caveat above: the caveat is guidance, not a guard, and the
 * documentation is only correct while that stays true. Enabling the NVIDIA GenAI pipelines in a
 * restricted-partition template must still pass `getConfig()` and still emit the pipeline nested stacks.
 * A hard guard added later would make every one of those pages wrong.
 */
describe("no partition guard blocks the NVIDIA GenAI pipelines", () => {
    const TEMPLATES: Record<string, { base: unknown; region: string }> = {
        govcloud: { base: govcloudTemplate, region: "us-gov-west-1" },
        eusovereign: { base: eusovereignTemplate, region: "eusc-de-east-1" },
    };

    /** A `getConfig()` call over a chosen template, with the placeholders it requires filled. */
    const runGetConfig = (name: string, mutate?: (c: any) => void): (() => void) => {
        const { base, region } = TEMPLATES[name];
        const config = JSON.parse(JSON.stringify(base)) as any;
        config.env.account = "123456789012";
        config.env.region = region;
        config.app.baseStackName = "t1doc";
        config.app.adminUserId = "t1-admin";
        config.app.adminEmailAddress = "t1-admin@example.com";
        if (config.app.useAlb?.enabled) {
            config.app.useAlb.domainHost = "vams-t1.example.com";
            config.app.useAlb.certificateArn =
                "arn:aws:acm:us-east-1:123456789012:certificate/" +
                "11111111-2222-3333-4444-555555555555";
            config.app.useAlb.optionalHostedZoneId = "";
        }
        mutate?.(config);
        return () => {
            (globalThis as any)[CONFIG_OVERRIDE_KEY] = JSON.stringify(config);
            try {
                Config.getConfig(newTestApp());
            } finally {
                delete (globalThis as any)[CONFIG_OVERRIDE_KEY];
            }
        };
    };

    test.each(RESTRICTED_TEMPLATES)(
        "%s accepts the shipped template unchanged",
        (name: TemplateName) => {
            // Positive control for the assertion below: proves the harness produces a config that
            // getConfig() accepts, so a later "does not throw" result is about the NVIDIA settings
            // rather than about a template this harness never got past.
            expect(runGetConfig(name)).not.toThrow();
        }
    );

    test.each(RESTRICTED_TEMPLATES)(
        "%s accepts the NVIDIA GenAI pipelines when enabled",
        (name: TemplateName) => {
            expect(runGetConfig(name, enableNvidiaPipelines)).not.toThrow();
        }
    );

    test.each(RESTRICTED_TEMPLATES)(
        "%s still rejects an enabled variant with an empty instanceTypes array",
        (name: TemplateName) => {
            // The shape validation the documentation describes as the only check must keep firing, so a
            // doc change cannot coincide with a validation regression.
            const run = runGetConfig(name, (c) => {
                enableNvidiaPipelines(c);
                c.app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.instanceTypes = [];
            });
            expect(run).toThrow(/nano16B\.instanceTypes must be a non-empty array/);
        }
    );

    test.each(RESTRICTED_TEMPLATES)(
        "%s emits the Cosmos and Gr00t pipeline nested stacks",
        (name: TemplateName) => {
            const stacks = Object.keys(synthWithNvidia(name).templates);
            expect(stacks.filter((s) => /CosmosBuilder/i.test(s))).not.toEqual([]);
            expect(stacks.filter((s) => /Gr00tBuilder/i.test(s))).not.toEqual([]);
        }
    );
});

/**
 * Grounds the execution-target caveat in the policy the deployment actually holds.
 *
 * The documentation names a specific IAM scope. If the wildcard is ever tightened, the pages become wrong
 * in the direction that makes an operator over-estimate the reach a pipeline author has, so both the
 * source that builds the policy and the emitted policy itself are asserted here.
 */
describe("the documented workflow-role target scope", () => {
    const source = readInfraSource(path.join("lib", "lambdaBuilder", "workflowFunctions.ts"));

    /** Every statement of the VAMSWorkflowIAMRole inline policies, across the whole assembly. */
    const workflowRoleStatements = (synth: SynthResult): any[] =>
        synth
            .ofType("AWS::IAM::Role")
            .filter((r) => r.properties.Description === "VAMS Workflow IAM Role.")
            .flatMap((r) => (r.properties.Policies ?? []) as any[])
            .flatMap((p) => (p.PolicyDocument?.Statement ?? []) as any[]);

    test("the source that builds the workflow role was read", () => {
        // Positive control: every assertion below is a substring match, which an empty read satisfies.
        expect(source.length).toBeGreaterThan(0);
        expect(source).toContain("export function buildWorkflowRole");
        expect(source).toContain("VAMSWorkflowIAMRole");
    });

    test("the three target statements use the deployment-name wildcard", () => {
        expect(source).toContain('IAMArn("*" + config.name + "*").lambda');
        expect(source).toContain('IAMArn("*" + config.name + "*").sqs');
        expect(source).toContain('IAMArn("*" + config.name + "*").eventBus');
        expect(source).toContain('IAMArn("default").eventBus');
        expect(source).toContain('const BACKEND_GENERATED_NAME_PATTERN = "vams-*"');
    });

    test.each(RESTRICTED_TEMPLATES)(
        "%s emits the documented wildcard in the workflow role policy",
        (name: TemplateName) => {
            const statements = workflowRoleStatements(synthWithNvidia(name));
            // Positive control: an assembly with no such role would satisfy every match below.
            expect(statements.length).toBeGreaterThan(0);

            const resourcesFor = (action: string) =>
                statements
                    .filter((s) => SynthResult.flatten(s.Action).includes(action))
                    .flatMap((s) => (Array.isArray(s.Resource) ? s.Resource : [s.Resource]))
                    .map((r: any) => SynthResult.flatten(r))
                    .join(" ");

            expect(resourcesFor("lambda:InvokeFunction")).toContain(":function:*vams*");
            expect(resourcesFor("sqs:SendMessage")).toMatch(/:sqs:[^ ]*:\*vams\*/);
            expect(resourcesFor("events:PutEvents")).toContain(":event-bus/*vams*");
            expect(resourcesFor("events:PutEvents")).toContain(":event-bus/default");
        }
    );
});
