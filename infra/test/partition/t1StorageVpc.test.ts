/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier — storage / VPC shard. Assertions against the CloudFormation each shipped config template
 * actually emits, plus two construct-level assertions where no shipped template exercises the branch.
 *
 * This is the only validation GovCloud and the EU Sovereign Cloud get: no environment exists for
 * either, so a defect in this area ships and surfaces as a `CREATE_FAILED` partway through creating
 * the core stack, or — worse for the KMS case — as silent, delayed data loss on a live deployment.
 *
 * Fixes covered, from `docs/review/_consolidation/shards/t1-storage-vpc.json`:
 *
 * | Fix     | Finding       | Disposition              | Expected today |
 * | ------- | ------------- | ------------------------ | -------------- |
 * | FIX-057 | S7-DOCS-006   | FIX_WITH_CONSTRAINTS     | PASSES         |
 * | FIX-091 | S1-INFRA-057  | ALREADY_FIXED_VERIFY_ONLY| PASSES         |
 * | FIX-093 | S1-INFRA-109  | ALREADY_FIXED_VERIFY_ONLY| PASSES         |
 * | FIX-070 | S1-INFRA-032  | FIX_WITH_CONSTRAINTS     | PASSES         |
 * | FIX-032 | S1-INFRA-060  | FIX                      | PASSES         |
 *
 * Two axes decide what a shipped template can prove here, and both create vacuous-pass traps:
 *
 * - **`useKmsCmkEncryption.enabled`** is `false` in the commercial template and `true` in both
 *   restricted templates, so the commercial synth emits **no** `AWS::KMS::Key`. Any KMS property
 *   assertion run only against commercial passes while checking nothing.
 * - **`useGlobalVpc.enabled`** is `false` in the commercial template, so the VPC flow-log group does
 *   not exist there — and neither does the EKS pipeline, which requires the global VPC. The EKS
 *   assertions therefore run on a *hybrid* govcloud config built with `synthTemplate`'s `mutate`
 *   option (see the FIX-070 block).
 *
 * Every item now asserts its post-fix state. `it.failing` was used while FIX-032 was outstanding:
 * Jest requires the body to fail and fails the suite once it starts passing, which forces whoever
 * applies a fix to remove `.failing` in the same change.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import * as s3 from "aws-cdk-lib/aws-s3";
import { VamsSchemaRegistration } from "../../lib/nestedStacks/pipelines/constructs/vamsSchemaRegistration-construct";
import {
    ALL_TEMPLATES,
    RESTRICTED_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    expectAbsent,
    stubDockerBundling,
    synthTemplate,
} from "../support/templateSynth";
import { newTestApp } from "../support/testApp";

// A full-app synth is ~20-25 s and this file needs five of them (three shipped templates, plus the
// external-CMK and EKS hybrids).
jest.setTimeout(600_000);

const cached: Partial<Record<TemplateName, SynthResult>> = {};
const synth = (name: TemplateName): SynthResult => (cached[name] ??= synthTemplate(name));

const at = (r: Resource) => `${r.stack}/${r.logicalId}`;

/**
 * A govcloud config with the EKS pipeline switched on — the only way to reach the EKS constructs and
 * the pipeline log groups that declare a SHORTER retention than the aspect applies. No shipped
 * template enables `useRapidPipeline.useEks`, and EKS additionally needs the global VPC, which only
 * the restricted templates turn on. Cached per version string, so the two callers below that ask for
 * "1.31" share one synth.
 */
const withEks = (eksClusterVersion: string): SynthResult =>
    synthTemplate("govcloud", {
        mutateKey: `eks-${eksClusterVersion}`,
        mutate: (c) => {
            c.app.pipelines.useRapidPipeline.useEks.enabled = true;
            // Required once the pipeline is enabled: getConfig() rejects the shipped
            // placeholder image URI.
            c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
                "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
            c.app.pipelines.useRapidPipeline.useEks.eksClusterVersion = eksClusterVersion;
        },
    });

/* -------------------------------------------------------------------------------------------------
 * FIX-057 — the VAMS-generated KMS CMK must survive `cdk destroy`
 * ---------------------------------------------------------------------------------------------- */

describe("FIX-057: the VAMS-generated KMS CMK is retained on teardown", () => {
    /**
     * `storageBuilder-nestedStack.ts` creates the key with `removalPolicy: cdk.RemovalPolicy.RETAIN`,
     * so both restricted templates emit `DeletionPolicy: Retain` / `UpdateReplacePolicy: Retain`.
     * Every DynamoDB table and the asset/auxiliary/artefacts buckets are `RETAIN` and encrypted under
     * that key, so a `Delete` policy here would leave the retained data undecryptable once the key's
     * pending-deletion window expired — and an update-replace of the key (for example switching to
     * `optionalExternalCmkArn`) would schedule the OLD key for deletion while live retained data was
     * still encrypted under it.
     */
    const keys = (s: SynthResult) => s.ofType("AWS::KMS::Key");

    test("FIX-057: the restricted templates emit the CMK with DeletionPolicy/UpdateReplacePolicy Retain", () => {
        const offenders: string[] = [];
        let total = 0;
        for (const name of RESTRICTED_TEMPLATES) {
            const found = keys(synth(name));
            total += found.length;
            for (const k of found) {
                if (k.raw.DeletionPolicy !== "Retain") {
                    offenders.push(`${name}:${at(k)} DeletionPolicy=${k.raw.DeletionPolicy}`);
                }
                if (k.raw.UpdateReplacePolicy !== "Retain") {
                    offenders.push(
                        `${name}:${at(k)} UpdateReplacePolicy=${k.raw.UpdateReplacePolicy}`
                    );
                }
            }
        }
        // In-band count, so a synth that emitted no key cannot satisfy the loop above trivially.
        // Both restricted templates set useKmsCmkEncryption.enabled with no external ARN, so one
        // VAMS-generated key each.
        expect(total).toBe(2);
        expect(offenders).toEqual([]);
    });

    test("commercial emits no CMK at all — the pairing that makes the restricted assertion meaningful", () => {
        // The commercial template ships useKmsCmkEncryption.enabled=false. Without this pairing, a
        // KMS assertion written against the commercial output would find nothing and pass.
        const restrictedKeys = RESTRICTED_TEMPLATES.reduce(
            (n, name) => n + keys(synth(name)).length,
            0
        );
        expectAbsent(
            "AWS::KMS::Key in the commercial template",
            keys(synth("commercial")).map(at),
            {
                description: "the restricted templates emit a VAMS-generated CMK",
                count: restrictedKeys,
            }
        );
    });

    test("no template emits a KMS Alias — the machine-checkable form of the owner's no-collision precondition", () => {
        // The owner's condition for RETAIN was "as long as we don't get collisions on a new
        // deployment". A retained key is addressed only by its generated key id/ARN; the moment
        // someone adds a friendly alias, a redeploy collides on the alias name and the precondition
        // is void. Control: the same synth emits KMS resources, so an alias would be visible here.
        for (const name of ALL_TEMPLATES) {
            const s = synth(name);
            if (keys(s).length === 0) {
                // Commercial: no CMK, hence nothing an alias could attach to. Stated rather than
                // silently passing.
                expect(name).toBe("commercial");
                expect(s.countOfType("AWS::KMS::Alias")).toBe(0);
                continue;
            }
            expectAbsent(`AWS::KMS::Alias in ${name}`, s.ofType("AWS::KMS::Alias").map(at), {
                description: `${name} emits KMS resources at all`,
                count: keys(s).length,
            });
        }
    });

    test("an externally supplied CMK ARN creates no stack-managed key (the uninstall doc's second case)", () => {
        // `kms.Key.fromKeyArn` imports, so the removal policy is a no-op for this path — which is why
        // the uninstall guidance has to distinguish the two cases. Hybrid config: the govcloud
        // template ships optionalExternalCmkArn=null, so the imported-key branch is not reachable
        // from any shipped template.
        const imported = synthTemplate("govcloud", {
            mutateKey: "external-cmk",
            mutate: (c) => {
                c.app.useKmsCmkEncryption.optionalExternalCmkArn =
                    "arn:aws-us-gov:kms:us-gov-west-1:123456789012:key/11111111-2222-3333-4444-555555555555";
            },
        });
        expectAbsent(
            "AWS::KMS::Key when optionalExternalCmkArn is supplied",
            imported.ofType("AWS::KMS::Key").map(at),
            {
                description: "the same template WITHOUT an external ARN emits a VAMS-generated key",
                count: keys(synth("govcloud")).length,
            }
        );
        // And the imported ARN is genuinely in use, so "no key" means "imported", not "unencrypted".
        const usingImported = imported
            .ofType("AWS::DynamoDB::Table")
            .filter((t) =>
                /key\/11111111-2222-3333-4444-555555555555/.test(
                    SynthResult.flatten(t.properties.SSESpecification?.KMSMasterKeyId)
                )
            );
        expect(usingImported.length).toBeGreaterThan(0);
    });

    test("the data the retained key protects is itself retained (tables + storage buckets)", () => {
        // "Retain the key so retained data stays decryptable" is only a real pairing if the data is
        // retained. Asserted per restricted template with in-band counts.
        const STORAGE_BUCKETS = [
            "AssetBucket",
            "AssetAuxiliaryBucket",
            "ArtefactsBucket",
            "AccessLogsBucket",
        ];
        for (const name of RESTRICTED_TEMPLATES) {
            const s = synth(name);
            const tables = s.ofType("AWS::DynamoDB::Table");
            expect(tables.length).toBeGreaterThan(20);
            expect(tables.filter((t) => t.raw.DeletionPolicy !== "Retain").map(at)).toEqual([]);

            const storageBuckets = s
                .ofType("AWS::S3::Bucket")
                .filter(
                    (b) =>
                        /StorageResourcesBuilder/.test(b.stack) &&
                        STORAGE_BUCKETS.some((n) => b.logicalId.startsWith(n))
                );
            expect(storageBuckets.length).toBe(STORAGE_BUCKETS.length);
            expect(storageBuckets.filter((b) => b.raw.DeletionPolicy !== "Retain").map(at)).toEqual(
                []
            );
        }
    });
});

/* -------------------------------------------------------------------------------------------------
 * FIX-091 — audit log groups get the aspect's ONE_YEAR, and declare it too
 * ---------------------------------------------------------------------------------------------- */

describe("FIX-091: every audit log group emits the documented one-year retention", () => {
    /**
     * ALREADY_FIXED_VERIFY_ONLY. `LogRetentionAspect` assigns `retentionInDays` unconditionally, and
     * the 24 construct-level declarations were aligned DOWN to `ONE_YEAR` so reading a construct no
     * longer implies a retention the deployment does not get. The finding's original TEN_YEARS
     * declaration at `storageBuilder-nestedStack.ts:648` is now `ONE_YEAR`.
     *
     * A failure here is a real regression: either the aspect value moved without sweeping the
     * declarations, or a group escaped the aspect.
     */
    const ONE_YEAR = 365;
    const TEN_YEARS = 3653;
    const CDK_DEFAULT = 731;

    /** The nine audit groups plus the orchestration-bus group, all created in storageBuilder. */
    const AUDIT_PREFIXES = [
        "/aws/vendedlogs/VAMSAuditAuthentication-",
        "/aws/vendedlogs/VAMSAuditAuthorization-",
        "/aws/vendedlogs/VAMSAuditFileUpload-",
        "/aws/vendedlogs/VAMSAuditFileDownload-",
        "/aws/vendedlogs/VAMSAuditFileDownloadStreamed-",
        "/aws/vendedlogs/VAMSAuditAuthOther-",
        "/aws/vendedlogs/VAMSAuditAuthChanges-",
        "/aws/vendedlogs/VAMSAuditActions-",
        "/aws/vendedlogs/VAMSAuditErrors-",
        "/aws/vendedlogs/VAMSOrchestrationBusAudit-",
    ];

    test.each(ALL_TEMPLATES)("%s: all ten audit groups are present and set to 365 days", (name) => {
        const s = synth(name);
        const groups = s.ofType("AWS::Logs::LogGroup");
        // In-band floor: a nested stack that failed to synthesize would otherwise let the
        // per-prefix loop below find nothing and the `every()` style checks pass trivially.
        expect(groups.length).toBeGreaterThanOrEqual(AUDIT_PREFIXES.length);

        const missing: string[] = [];
        const wrong: string[] = [];
        for (const prefix of AUDIT_PREFIXES) {
            const match = groups.filter((g) =>
                SynthResult.flatten(g.properties.LogGroupName).startsWith(prefix)
            );
            if (match.length !== 1) {
                missing.push(`${prefix} (found ${match.length})`);
                continue;
            }
            if (match[0].properties.RetentionInDays !== ONE_YEAR) {
                wrong.push(`${prefix} => ${match[0].properties.RetentionInDays}`);
            }
        }
        expect(missing).toEqual([]);
        expect(wrong).toEqual([]);
    });

    test("no log group in any template is left at TEN_YEARS or at the CDK default", () => {
        // The negative control that makes the assertion above meaningful: a bare CDK LogGroup emits
        // 731, NOT an absent value, so 731 is what a group the aspect missed would look like; 3653 is
        // what a reverted aspect value would look like.
        for (const name of ALL_TEMPLATES) {
            const s = synth(name);
            const groups = s.ofType("AWS::Logs::LogGroup");
            expect(groups.length).toBeGreaterThan(0);
            expect(
                groups
                    .filter((g) =>
                        [TEN_YEARS, CDK_DEFAULT].includes(g.properties.RetentionInDays as number)
                    )
                    .map((g) => `${name}:${at(g)}=${g.properties.RetentionInDays}`)
            ).toEqual([]);
            // And the distinct set really is a single value, so nothing sits at an unexpected third.
            expect(Array.from(new Set(groups.map((g) => g.properties.RetentionInDays)))).toEqual([
                ONE_YEAR,
            ]);
        }
    });
});

/* -------------------------------------------------------------------------------------------------
 * FIX-093 — the VPC flow-log group, asserted as a pair
 * ---------------------------------------------------------------------------------------------- */

describe("FIX-093: the VPC flow-log group carries one-year retention where the VPC exists", () => {
    /**
     * ALREADY_FIXED_VERIFY_ONLY. `vpcBuilder-nestedStack.ts:337` declares `ONE_YEAR` and the aspect
     * applies `ONE_YEAR`, so declaration and deployment agree.
     *
     * The group is created only inside `if (config.app.useGlobalVpc.enabled)`, which the commercial
     * template leaves `false`. An assertion against the commercial output alone cannot tell "retention
     * correctly set" from "group never emitted" — hence the paired form: present-with-365 in both
     * restricted templates, absent from commercial with a control proving the search works there.
     */
    const FLOW_LOG_PREFIX = "/aws/vendedlogs/VAMSCloudWatchVPCLogs";
    const flowLogGroups = (s: SynthResult) =>
        s.where("AWS::Logs::LogGroup", (g) =>
            SynthResult.flatten(g.properties.LogGroupName).startsWith(FLOW_LOG_PREFIX)
        );

    test.each(RESTRICTED_TEMPLATES)("%s emits the flow-log group with 365 days", (name) => {
        const s = synth(name);
        const found = flowLogGroups(s);
        expect(found.map(at)).toHaveLength(1);
        expect(found[0].properties.RetentionInDays).toBe(365);
        // The VPC really is on in this template — otherwise the group's presence would be surprising
        // rather than expected.
        expect(s.countOfType("AWS::EC2::VPC")).toBeGreaterThan(0);
    });

    test("commercial emits no flow-log group, because it ships with useGlobalVpc disabled", () => {
        const s = synth("commercial");
        expect(s.countOfType("AWS::EC2::VPC")).toBe(0);
        expectAbsent("VPC flow-log group in commercial", flowLogGroups(s).map(at), {
            // Control: the commercial template DOES emit log groups, so an absent flow-log group is
            // the VPC branch being off, not the query being broken.
            description: "commercial emits CloudWatch log groups at all",
            count: s.countOfType("AWS::Logs::LogGroup"),
        });
    });

    test("a construct that declares a SHORTER retention is still swept to 365 by the aspect", () => {
        // The aspect's cost consequence, made explicit rather than discovered on a bill. Three
        // pipeline constructs deliberately declare a shorter window for high-volume container logs
        // (rapidPipelineEKS TWO_WEEKS=14, modelOps and rapidPipeline ONE_MONTH=30) and the aspect
        // overwrites all of them. None is reachable from a shipped template — all three pipelines are
        // disabled in all three configs — so this uses the EKS hybrid.
        const s = withEks("1.31");
        const declaredShort = s.where(
            "AWS::Logs::LogGroup",
            (g) => /RapidPipelineEKS/.test(g.stack) && /StateMachineLogGroup/.test(g.logicalId)
        );
        // In-band count: without it, a hybrid that failed to emit the EKS stack would satisfy the
        // retention check below trivially.
        expect(declaredShort.map(at)).toHaveLength(1);
        expect(declaredShort[0].properties.RetentionInDays).toBe(365);
        // Control that 14 is what the declaration alone would have produced: no group anywhere in the
        // hybrid carries the declared TWO_WEEKS value.
        expect(
            s
                .ofType("AWS::Logs::LogGroup")
                .filter((g) => g.properties.RetentionInDays === 14)
                .map(at)
        ).toEqual([]);
    });
});

/* -------------------------------------------------------------------------------------------------
 * FIX-070 — eksClusterVersion must reach the EKS cluster
 * ---------------------------------------------------------------------------------------------- */

describe("FIX-070: the EKS cluster version comes from config, not a hardcoded constant", () => {
    /**
     * `rapidPipelineEKS-construct.ts` builds the cluster version with
     * `eks.KubernetesVersion.of(config.app.pipelines.useRapidPipeline.useEks.eksClusterVersion)`, so
     * the value an operator sets in `config.json` is the version the control plane is created at.
     *
     * TWO version strings, deliberately. A single assertion on one value cannot separate a wired
     * config read from a constant that happens to equal it — the previous hardcoded
     * `KubernetesVersion.V1_31` satisfies any assertion written only against "1.31". Synthesizing the
     * same construct twice from two different config values, and getting two different emitted
     * versions, is the discrimination: no constant can produce both.
     *
     * HYBRID CONFIG. `useRapidPipeline.useEks.enabled` is `false` in every shipped template, so the
     * EKS constructs are never emitted from a shipped config and an assertion on the as-shipped
     * output would find nothing. EKS also requires the global VPC, which only the restricted
     * templates enable — so govcloud is the base and `useEks.enabled` + `eksClusterVersion` are the
     * only deviations. The finding is not partition-sensitive (its axis is `vpc`).
     *
     * The emitted resource is `Custom::AWSCDK-EKS-Cluster` (the legacy `eks.Cluster` provisions the
     * control plane through a custom resource), and the version lives at `Properties.Config.version`.
     */
    const eksVersions = (s: SynthResult): string[] =>
        s
            .ofType("Custom::AWSCDK-EKS-Cluster")
            .map((r) => SynthResult.flatten(r.properties.Config?.version));

    test("the EKS cluster and the version property path exist (property-path control)", () => {
        // PATH control: proves the resource type and property path used below are real. Without it,
        // `eksVersions()` returning [] would make the assertions fail for the wrong reason, or (written
        // as a negative) pass vacuously. The single-element array is also the in-band count — one
        // cluster, so a hybrid that emitted none or several is caught here.
        expect(eksVersions(withEks("1.31"))).toEqual(["1.31"]);
    });

    test("FIX-070: a configured eksClusterVersion of 1.32 reaches the cluster", () => {
        // The other half of the pair. With the version hardcoded this returned ["1.31"]; only a config
        // read can return 1.32 here while the assertion above still returns 1.31.
        expect(eksVersions(withEks("1.32"))).toEqual(["1.32"]);
    });
});

/* -------------------------------------------------------------------------------------------------
 * FIX-032 — a vamsSchema bundle with no workflow.json
 * ---------------------------------------------------------------------------------------------- */

describe("FIX-032: VamsSchemaRegistration accepts a bundle with no workflow.json", () => {
    /**
     * The construct used to throw at synth (`VamsSchemaRegistration: required workflow.json not found
     * in <dir>`) and to set `bundleS3Keys.workflow` unconditionally. workflow.json is now truly
     * optional, matching the steering doc and the backend, which already handles the case
     * (`assemble_bundle` fetches `keys['workflow']` only `if keys.get('workflow')`). The remaining
     * cases — templates alongside a workflow-less bundle, the schemaHash reacting to a workflow.json
     * appearing, and pipeline.json still being required — are in
     * `vamsSchemaRegistrationOptionalWorkflow.test.ts`.
     *
     * NOT a T1 assertion, deliberately: every shipped bundle has a workflow.json, so all three config
     * templates emit identical registrations and a full-app synth adds nothing. The branch only
     * exists for a bundle that does not ship, so it is asserted at the construct level.
     *
     * The second half of the assertion is the deploy-time trap: if the required-file loop is relaxed
     * without also making `bundleS3Keys.workflow` conditional, the import lambda receives a key for
     * an object BucketDeployment never uploaded, `_read_s3_json` raises, and the custom resource
     * returns FAILED — a nested-stack rollback rather than a soft skip.
     */
    const PIPELINE_JSON = JSON.stringify(
        {
            pipelineName: "T1 Workflowless Pipeline",
            category: "Conversion",
            description: "Fixture bundle carrying only pipeline.json.",
            executionConfig: {
                executionType: "Lambda",
                waitForCallback: "Disabled",
                taskTimeout: "900",
                lambda: {},
            },
        },
        null,
        2
    );
    const WORKFLOW_JSON = JSON.stringify({ workflowName: "T1 Fixture Workflow" }, null, 2);

    let tmpRoot: string;

    beforeAll(() => {
        // BucketDeployment stages the bundle dir as an asset; stub the eager DockerImage.fromBuild the
        // same way the T1 harness does so nothing here needs a Docker daemon.
        stubDockerBundling();
        tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "vams-t1-vamsschema-"));
    });

    afterAll(() => {
        fs.rmSync(tmpRoot, { recursive: true, force: true });
    });

    /** A bundle dir on disk; `withWorkflow` decides whether workflow.json is present. */
    const bundleDir = (name: string, withWorkflow: boolean): string => {
        const dir = path.join(tmpRoot, name);
        fs.mkdirSync(dir, { recursive: true });
        fs.writeFileSync(path.join(dir, "pipeline.json"), PIPELINE_JSON);
        if (withWorkflow) fs.writeFileSync(path.join(dir, "workflow.json"), WORKFLOW_JSON);
        return dir;
    };

    /** Registration CR properties, with bundleS3Keys parsed back out of its JSON string. */
    const registrationKeys = (dir: string, id: string) => {
        const app = newTestApp();
        const stack = new cdk.Stack(app, `VamsSchemaFixture${id}`, {
            env: { account: "123456789012", region: "us-east-1" },
        });
        const bucket = new s3.Bucket(stack, "Artefacts");
        new VamsSchemaRegistration(stack, `Registration${id}`, {
            importFunctionName: "t1-import-fn",
            artefactsBucket: bucket,
            vamsSchemaDir: dir,
            idOverrides: { pipelineId: "t1-fixture-pipeline" },
        });
        const resources: Record<string, { Properties?: Record<string, string> }> =
            Template.fromStack(stack).findResources("AWS::CloudFormation::CustomResource");
        const entries = Object.values(resources)
            .map((r) => r.Properties?.bundleS3Keys)
            .filter((v): v is string => typeof v === "string");
        // Exactly one registration CR, so "no workflow key" cannot be satisfied by there being no CR.
        expect(entries).toHaveLength(1);
        return JSON.parse(entries[0]) as {
            pipeline: string;
            workflow?: string;
            templates?: string[];
        };
    };

    test("a bundle WITH workflow.json registers both — the positive control", () => {
        // Without this, "no workflow key" below would be satisfied by a construct that emitted no
        // registration at all, or by a property-name typo in the assertion.
        const keys = registrationKeys(bundleDir("with-workflow", true), "WithWorkflow");
        expect(keys.pipeline).toMatch(/\/pipeline\.json$/);
        expect(keys.workflow).toMatch(/\/workflow\.json$/);
    });

    test("FIX-032: a bundle with only pipeline.json synthesizes and omits bundleS3Keys.workflow", () => {
        // Synthesizing at all is half of it; omitting the key is the other half, since a key pointing
        // at an object BucketDeployment never uploaded fails the custom resource mid-deploy instead.
        const keys = registrationKeys(bundleDir("no-workflow", false), "NoWorkflow");
        expect(keys.pipeline).toMatch(/\/pipeline\.json$/);
        expect("workflow" in keys).toBe(false);
    });
});

/* -------------------------------------------------------------------------------------------------
 * FIX-070 (second half) — the version-shape guard lives in `test/eksClusterVersionConfig.test.ts`
 *
 * Wiring the value through converts a silently-ignored field into a deploy failure unless
 * `getConfig()` checks it: `eks.KubernetesVersion.of("9.99")` synthesizes happily and fails at cluster
 * creation. That assertion cannot live here. `getConfig()` reads `config/config.json` from disk, and
 * the only working interception is a module-scope `jest.mock("fs")` — `jest.spyOn(fs, "readFileSync")`
 * fails with `TypeError: Cannot redefine property: readFileSync` — and a module-scope fs mock in THIS
 * file would sit under every full-app synth above, asset staging included. It therefore has its own
 * file, which also keeps the ~20 s-per-synth suite separate from a sub-second validation suite.
 * ---------------------------------------------------------------------------------------------- */
