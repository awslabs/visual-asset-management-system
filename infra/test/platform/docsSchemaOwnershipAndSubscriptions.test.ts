/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Documentation-only fixes need a test for the same reason code fixes do: nothing else notices when the
 * page is reworded, moved, or when the behaviour it describes changes underneath it. Three items are
 * covered, each paired with an assertion against the source that produces the documented behaviour, so a
 * later change to the code fails here rather than leaving a page quietly wrong.
 *
 * - **FIX-086** — asset notification subscriptions accept a recipient that is not a VAMS user. The owner's
 *   decision is to leave the handler alone (subscription management is an administrative form) and to
 *   record the behaviour in the DEVELOPER documentation.
 * - **FIX-087** — the deploy-time schema import replaces an operator-modified built-in pipeline, workflow,
 *   or template. The owner's decision is to leave the importer alone and to state that built-ins are
 *   schema-owned by the CDK, including that a front-end change — disabling among them — is reverted.
 * - **FIX-093** — the VPC flow-log group's construct-level retention is dead configuration because
 *   `LogRetentionAspect` overwrites it. The construct declaration is already aligned; what is asserted
 *   here is that the pages describing that log group say where the period actually comes from. The
 *   EMITTED value is asserted in `t1LogGroupRetentionInventory.test.ts`.
 *
 * Every assertion below is a substring match, which an empty or renamed file satisfies. Each block
 * therefore opens with a control that reads the file and pins a heading that predates this change.
 */

import * as fs from "fs";
import * as path from "path";

const REPO = path.join(__dirname, "..", "..", "..");
const DOCS = path.join(REPO, "documentation", "docusaurus-site", "docs");

const readDoc = (rel: string): string => fs.readFileSync(path.join(DOCS, rel), "utf8");
const readRepo = (rel: string): string => fs.readFileSync(path.join(REPO, rel), "utf8");

/**
 * One Markdown section, from its heading to the next heading of the same or a higher level.
 *
 * Matching against the whole page would let a fact stated anywhere satisfy an assertion about a specific
 * section — including a fact that happens to sit in an unrelated part of the page.
 */
function section(text: string, heading: string): string {
    const start = text.indexOf(heading);
    if (start < 0) return "";
    const level = (heading.match(/^#+/) ?? ["##"])[0].length;
    const lines = text.slice(start + heading.length).split("\n");
    const out: string[] = [];
    for (const line of lines) {
        const m = line.match(/^(#+)\s/);
        if (m && m[1].length <= level) break;
        out.push(line);
    }
    return out.join("\n");
}

/** A Docusaurus admonition body, addressed by its title line. */
function admonitionBody(text: string, title: string): string {
    const start = text.indexOf(title);
    if (start < 0) return "";
    const bodyStart = start + title.length;
    const end = text.indexOf("\n:::", bodyStart);
    return end < 0 ? "" : text.slice(bodyStart, end);
}

/* -------------------------------------------------------------------------------------------------
 * FIX-086 — notification subscription recipients
 * ---------------------------------------------------------------------------------------------- */

const SUBSCRIPTION_HEADING = "## Notification Subscriptions";
const SUBSCRIPTION_CAVEAT_TITLE = ":::warning[A subscriber does not have to be a VAMS user]";
const SUBSCRIPTION_SERVICE = path.join(
    "backend",
    "backend",
    "handlers",
    "subscription",
    "subscriptionService.py"
);

describe("FIX-086: the subscription recipient behaviour is recorded in the developer documentation", () => {
    test("developer/backend.md was read and still carries its pre-existing headings", () => {
        const text = readDoc("developer/backend.md");
        expect(text).toContain("# Backend Development");
        expect(text).toContain("## Input Validation");
        expect(text).toContain(SUBSCRIPTION_HEADING);
    });

    test("developer/backend.md names the validator, the fallback, and the unbounded list", () => {
        const body = section(readDoc("developer/backend.md"), SUBSCRIPTION_HEADING);
        expect(body).toContain("`USERID_ARRAY`");
        expect(body).toContain("get_userProfile_Email()");
        expect(body).toMatch(/no maximum length/);
        // The delivery step that bounds the exposure. A page that omits it overstates the reach.
        expect(body).toContain(SUBSCRIPTION_CAVEAT_TITLE);
        expect(admonitionBody(readDoc("developer/backend.md"), SUBSCRIPTION_CAVEAT_TITLE)).toMatch(
            /confirmation request/
        );
    });

    test("developer/backend.md records that subscription management is an administrative form", () => {
        const body = admonitionBody(readDoc("developer/backend.md"), SUBSCRIPTION_CAVEAT_TITLE);
        expect(body).toMatch(/administrative form/);
        expect(body).toContain("/auth/subscriptions");
        // The half that is easy to get wrong: the API route is NOT administrator-only, so a page that
        // says only "admin form" would understate who can add a recipient.
        expect(body).toContain("`database-user`");
    });

    test("developer/security.md frames it as the boundary of the two-tier model", () => {
        const text = readDoc("developer/security.md");
        expect(text).toContain("# Security: Developer Reference");
        expect(text).toContain("### Stage 4: Casbin evaluates two tiers");
        const heading = "## What the two tiers do not cover: notification recipients";
        expect(text).toContain(heading);
        const body = section(text, heading);
        expect(body).toContain("`USERID_ARRAY`");
        expect(body).toContain("/auth/subscriptions");
        // Cross-link rather than a second copy of the mechanism.
        expect(body).toContain("./backend.md#notification-subscriptions");
    });

    test("the handler still behaves the way both pages describe", () => {
        // The pages claim shape-only validation with a verbatim-email fallback and an Amazon SNS email
        // subscription. A change to any of the three makes the documentation wrong, so it fails here.
        const source = readRepo(SUBSCRIPTION_SERVICE);
        expect(source.length).toBeGreaterThan(0);
        expect(source).toContain("'validator': 'USERID_ARRAY'");
        expect(source).toContain("def get_userProfile_Email");
        expect(source).toContain("'validator': 'EMAIL'");
        expect(source).toContain("email = userId");
        expect(source).toContain("Protocol='email'");
        // The removal asymmetry the page records: the update path unsubscribes by RESOLVED address,
        // while `/unsubscribe` matches the value as submitted. If either side changes, the paragraph
        // describing it is wrong.
        expect(source).toContain('delete_sns_subscriptions(body["entityId"], list(emailsDeleted)');
        const unsubscribe = readRepo(
            path.join("backend", "backend", "handlers", "subscription", "unsubscribeService.py")
        );
        expect(unsubscribe).toContain(
            'delete_sns_subscriptions(body["entityId"], list(body["subscribers"])'
        );
    });

    test("no shipped role template grants the /auth/subscriptions web route", () => {
        const dir = path.join(REPO, "documentation", "permissionsTemplates");
        const files = fs.readdirSync(dir).filter((f) => f.endsWith(".json"));
        // Control: the glob found the templates. Zero files would satisfy the absence assertion.
        expect(files.length).toBeGreaterThanOrEqual(6);

        let webConstraints = 0;
        let apiPostSubscriptions = 0;
        const grantsSubscriptionPage: string[] = [];
        for (const file of files) {
            const doc = JSON.parse(fs.readFileSync(path.join(dir, file), "utf8"));
            for (const constraint of doc.constraints ?? []) {
                const criteria = [
                    ...(constraint.criteriaAnd ?? []),
                    ...(constraint.criteriaOr ?? []),
                ];
                const values = criteria.map((c: any) => String(c.value ?? ""));
                const actions = (constraint.groupPermissions ?? []).map((g: any) => g.action);
                if (constraint.objectType === "web") {
                    webConstraints++;
                    if (values.some((v: string) => v.startsWith("/auth/subscriptions"))) {
                        grantsSubscriptionPage.push(`${file}: ${constraint.name}`);
                    }
                }
                if (
                    constraint.objectType === "api" &&
                    values.includes("/subscriptions") &&
                    actions.includes("POST")
                ) {
                    apiPostSubscriptions++;
                }
            }
        }
        // Second control: the scan understands the template shape, so the absence above is meaningful.
        expect(webConstraints).toBeGreaterThan(0);
        // The documented asymmetry: write access to the API route IS granted by a non-admin template.
        expect(apiPostSubscriptions).toBeGreaterThan(0);
        expect(grantsSubscriptionPage).toEqual([]);
    });
});

/* -------------------------------------------------------------------------------------------------
 * FIX-087 — built-in pipelines, workflows and templates are schema-owned by the CDK
 * ---------------------------------------------------------------------------------------------- */

const SCHEMA_OWNED_TITLE =
    ":::warning[Built-in pipelines, workflows, and templates are schema-owned by the CDK]";
const REREGISTER_TITLE =
    ":::warning[Built-in pipelines and workflows are re-registered from the CDK schema]";

describe("FIX-087: built-in ownership is documented where an operator will look", () => {
    test("concepts/pipelines-and-workflows.md was read and still carries its headings", () => {
        const text = readDoc("concepts/pipelines-and-workflows.md");
        expect(text).toContain("# Pipelines and Workflows");
        expect(text).toContain("### GLOBAL pipelines versus database-specific pipelines");
        expect(text).toContain(SCHEMA_OWNED_TITLE);
    });

    test("the concepts page states that a front-end change, including disabling, is reverted", () => {
        const body = admonitionBody(
            readDoc("concepts/pipelines-and-workflows.md"),
            SCHEMA_OWNED_TITLE
        );
        expect(body).toContain("`vamsSchema`");
        expect(body).toMatch(/Disabling or archiving a built-in in the web interface/);
        expect(body).toMatch(/re-enables it/);
        // The durable alternative, or the reader is left with a prohibition and no action.
        expect(body).toContain("`autoRegisterWithVAMS`");
        expect(body).toContain("`autoRegisterAutoTriggerOnFileUpload`");
    });

    test("deployment/update-the-solution.md was read and still carries its update steps", () => {
        const text = readDoc("deployment/update-the-solution.md");
        expect(text).toContain("# Update the solution");
        expect(text).toContain("### Step 4: Deploy the update");
        // The sibling caveat this one is placed next to; if it disappears, the placement claim changes.
        expect(text).toContain(
            ":::warning[Default roles and constraints are overwritten on every deployment]"
        );
        expect(text).toContain(REREGISTER_TITLE);
    });

    test("the update page separates what a re-registration rewrites from what it preserves", () => {
        const body = admonitionBody(readDoc("deployment/update-the-solution.md"), REREGISTER_TITLE);
        // All four record kinds the importer touches. Naming only the pipeline would leave an operator
        // expecting an edited template or trigger to survive.
        expect(body).toMatch(/\*\*The pipeline\*\*/);
        expect(body).toMatch(/\*\*The workflow\*\*/);
        expect(body).toMatch(/\*\*Each template the bundle ships\*\*/);
        expect(body).toMatch(/\*\*Each trigger the bundle declares\*\*/);
        expect(body).toMatch(/enabled and unarchived/);
        // And the preserved set, which is the half that decides where a customization can safely live.
        expect(body).toMatch(/does \*\*not\*\* touch execution history/);
        expect(body).toMatch(/identifiers of your own/);
        expect(body).toContain("`SYSTEM_USER`");
        expect(body).toContain("`autoRegisterWithVAMS`");
    });

    test("the importer still forces the re-enable both pages describe", () => {
        const importer = readRepo(
            path.join(
                "backend",
                "backend",
                "handlers",
                "workflows",
                "importGlobalPipelineWorkflow.py"
            )
        );
        const builder = readRepo(
            path.join("backend", "backend", "common", "workflows", "vamsSchemaImport.py")
        );
        expect(importer.length).toBeGreaterThan(0);
        expect(builder.length).toBeGreaterThan(0);
        // The two halves of "written back as enabled and unarchived".
        expect(importer).toContain('update_body["archived"] = False');
        expect(builder).toContain('"enabled": True');
        // The templates and triggers the update page says are rewritten by id / by type.
        expect(builder).toContain('"kind": "template"');
        expect(builder).toContain('"kind": "trigger"');
    });

    test("the registration re-runs on a schema change, as the update page explains", () => {
        // `schemaHash` as a resource property is what makes CloudFormation re-invoke the custom
        // resource. Without it the page's "whenever a release revises the built-in" claim is false.
        const construct = readRepo(
            path.join(
                "infra",
                "lib",
                "nestedStacks",
                "pipelines",
                "constructs",
                "vamsSchemaRegistration-construct.ts"
            )
        );
        expect(construct).toContain('new cdk.CustomResource(this, "Registration"');
        expect(construct).toContain("schemaHash,");
        expect(construct).toContain("private hashSchema(");
    });
});

/* -------------------------------------------------------------------------------------------------
 * FIX-093 — the VPC flow-log group's retention comes from the aspect, not the construct
 * ---------------------------------------------------------------------------------------------- */

describe("FIX-093: the pages describing the VPC flow-log group name the real source of retention", () => {
    const CALL_SITE = "core-stack.ts";

    test("architecture/networking.md was read and still documents the flow-log group", () => {
        const text = readDoc("architecture/networking.md");
        expect(text).toContain("## VPC Flow Logs");
        expect(text).toContain("/aws/vendedlogs/VAMSCloudWatchVPCLogs-{hash}");
    });

    test("architecture/networking.md states the aspect overrides a construct declaration", () => {
        const body = section(readDoc("architecture/networking.md"), "## VPC Flow Logs");
        expect(body).toContain("`LogRetentionAspect`");
        expect(body).toMatch(/overwrites any value declared on an individual construct/);
        expect(body).toContain(CALL_SITE);
        expect(body).toMatch(/1 year/);
    });

    test("architecture/aws-resources.md keeps the same statement", () => {
        // Consistency guard rather than a second source of truth: the two pages must not disagree about
        // where the period comes from.
        const text = readDoc("architecture/aws-resources.md");
        expect(text).toContain(":::note[Log Retention]");
        const body = admonitionBody(text, ":::note[Log Retention]");
        expect(body).toContain("`LogRetentionAspect`");
        expect(body).toMatch(/has no effect/);
        expect(body).toContain(CALL_SITE);
    });

    test("developer/audit-logging.md remains the single place the change is described", () => {
        const text = readDoc("developer/audit-logging.md");
        expect(text).toContain("## Configuring Log Retention");
        expect(text).toContain("LogRetentionAspect");
        // Both pages above link here, so the anchor is load-bearing.
        expect(text).toMatch(/^#{2}\s+Configuring Log Retention$/m);
    });

    test("the aspect is applied once, with the one-year value the pages state", () => {
        const source = readRepo(path.join("infra", "lib", "core-stack.ts"));
        const calls = [
            ...source.matchAll(
                /new LogRetentionAspect\(\s*logs\.RetentionDays\.([A-Z_0-9]+)\s*\)/g
            ),
        ];
        // One call site is what makes "change it in one place" true.
        expect(calls.length).toBe(1);
        expect(calls[0][1]).toBe("ONE_YEAR");
    });

    test("the VPC flow-log group no longer declares a period the aspect contradicts", () => {
        // The FIX-093 subject. The declaration is dead configuration either way; a value that disagrees
        // with the aspect is what made reading the construct misleading.
        const source = readRepo(
            path.join("infra", "lib", "nestedStacks", "vpc", "vpcBuilder-nestedStack.ts")
        );
        const declarations = [...source.matchAll(/retention:\s*RetentionDays\.([A-Z_0-9]+)/g)].map(
            (m) => m[1]
        );
        // Control: the scan found the declaration at all.
        expect(declarations.length).toBeGreaterThan(0);
        expect(declarations).toEqual(declarations.map(() => "ONE_YEAR"));
    });
});
