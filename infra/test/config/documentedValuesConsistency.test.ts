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
