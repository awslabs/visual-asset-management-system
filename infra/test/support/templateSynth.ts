/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Synthesizes the whole VAMS app from a shipped config template and exposes every emitted
 * CloudFormation template for assertion — the "T1" validation tier.
 *
 * Why this exists. VAMS deploys to commercial AWS, GovCloud and the EU Sovereign Cloud, and a partition
 * defect is invisible in commercial synth, invisible in unit tests, and typically surfaces as a
 * `CREATE_FAILED` partway through creating the core stack. No GovCloud or EU Sovereign environment is
 * available, so a synth assertion against those templates is the only validation those partitions get.
 * Before this harness, nothing synthesized a stack from `config.template.govcloud.json` or
 * `config.template.eusovereign.json`: the three tests that import them all check `getConfig()`
 * validation or ConfigBuilder parity instead.
 *
 * Three things had to be solved to make full-app synth work in Jest, each of which will bite anyone
 * writing a similar harness:
 *
 * 1. **Docker.** `lambdaLayersBuilder-nestedStack.ts:36` calls `cdk.DockerImage.fromBuild()` as an
 *    *eager argument* to `bundling.image`, so it runs before CDK consults its bundling-skip logic —
 *    which means `aws:cdk:bundling-stacks: []` alone does NOT avoid the docker build. Both are needed:
 *    the context to skip `docker run`, and a stub on the static to skip `docker build`. (`infra.test.ts`
 *    does neither, which is why it fails whenever Docker Desktop is not running.)
 * 2. **Nested stacks.** VAMS puts nearly everything in nested stacks, so `Template.fromStack(root)`
 *    sees ~17 resources out of ~600. Assertions have to run over the synthesized assembly, not one
 *    stack.
 * 3. **Construct id must equal `stackName`.** `core-stack.ts` builds CDK Nag suppression paths from
 *    `props.stackName`, but `NagSuppressions` resolves them by *construct* path. `bin/infra.ts` always
 *    passes the same string for both; pass different ones and synth dies with "path did not match any
 *    resource".
 *
 * ## Paired assertions
 *
 * A negative assertion on a restricted partition ("no Cognito interface endpoint is emitted") cannot
 * distinguish correct behaviour from a template that emitted nothing at all. Every negative needs a
 * positive control on the commercial side, or a non-zero count of the same resource type. `expectAbsent`
 * takes that control as a required argument rather than an optional one, so it cannot be forgotten.
 */

import * as cdk from "aws-cdk-lib";
import * as cxapi from "aws-cdk-lib/cx-api";
import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as Infra from "../../lib/core-stack";
import { s3AssetBucketRecords } from "../../lib/helper/s3AssetBuckets";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import cdkJson from "../../cdk.json";
import { newTestApp } from "./testApp";
import { SplatToolboxConstruct } from "../../lib/nestedStacks/pipelines/3dRecon/splatToolbox/constructs/splatToolbox-construct";

export type TemplateName = "commercial" | "govcloud" | "eusovereign";

/** Region and partition each template is meant to deploy into. */
const TARGET: Record<TemplateName, { region: string; partition: string }> = {
    commercial: { region: "us-east-1", partition: "aws" },
    govcloud: { region: "us-gov-west-1", partition: "aws-us-gov" },
    eusovereign: { region: "eusc-de-east-1", partition: "aws-eusc" },
};

const RAW: Record<TemplateName, unknown> = {
    commercial: commercialTemplate,
    govcloud: govcloudTemplate,
    eusovereign: eusovereignTemplate,
};

const ACCOUNT = "123456789012";

export interface Resource {
    logicalId: string;
    stack: string;
    type: string;
    properties: Record<string, any>;
    raw: Record<string, any>;
}

/** A synth-time annotation, as the AWS CDK CLI prints it while synthesizing or deploying. */
export interface SynthWarning {
    /** Construct path the annotation was attached to, e.g. `/stack/AuthBuilder/.../Construct`. */
    path: string;
    message: string;
}

/** Every resource in every emitted template, with helpers for querying across all of them. */
export class SynthResult {
    constructor(
        readonly name: TemplateName,
        readonly partition: string,
        readonly region: string,
        readonly templates: Record<string, any>,
        readonly resources: Resource[],
        readonly warnings: SynthWarning[] = []
    ) {}

    ofType(type: string): Resource[] {
        return this.resources.filter((r) => r.type === type);
    }

    countOfType(type: string): number {
        return this.ofType(type).length;
    }

    /** Distinct resource types present, for diagnosing an unexpectedly empty query. */
    types(): string[] {
        return Array.from(new Set(this.resources.map((r) => r.type))).sort();
    }

    where(type: string, predicate: (r: Resource) => boolean): Resource[] {
        return this.ofType(type).filter(predicate);
    }

    /** Full-text search across every template, for properties buried in Fn::Join or Fn::Sub. */
    grep(needle: string | RegExp): { stack: string; logicalId: string; type: string }[] {
        const re = typeof needle === "string" ? new RegExp(escapeRegExp(needle)) : needle;
        return this.resources
            .filter((r) => re.test(JSON.stringify(r.raw)))
            .map((r) => ({ stack: r.stack, logicalId: r.logicalId, type: r.type }));
    }

    /**
     * A directive/property value assembled through Fn::Join, flattened to a string.
     *
     * A raw substring search on the template finds the literal prefix and then a token boundary, so an
     * assertion written that way passes while checking nothing — this is how a CSP `script-src` check
     * silently succeeded against a policy whose hashes were in separate Fn::Join parts.
     */
    static flatten(value: any): string {
        if (typeof value === "string") return value;
        if (value === null || value === undefined) return "";
        if (Array.isArray(value)) return value.map((v) => SynthResult.flatten(v)).join("");
        if (typeof value === "object") {
            if (value["Fn::Join"]) {
                const [sep, parts] = value["Fn::Join"];
                return (parts as any[]).map((p) => SynthResult.flatten(p)).join(sep);
            }
            if (value.Ref) return `\${${value.Ref}}`;
            if (value["Fn::GetAtt"]) return `\${${(value["Fn::GetAtt"] as any[]).join(".")}}`;
            if (value["Fn::Sub"]) return SynthResult.flatten(value["Fn::Sub"]);
            return Object.values(value)
                .map((v) => SynthResult.flatten(v))
                .join("");
        }
        return String(value);
    }
}

function escapeRegExp(s: string): string {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** A deployable Config from a shipped template, with the placeholders getConfig() would fill. */
function buildConfig(name: TemplateName, mutate?: (c: any) => void): Config.Config {
    const t = TARGET[name];
    const config = JSON.parse(JSON.stringify(RAW[name])) as Config.Config;
    const stackName = `vams-t1-${name}`;

    config.env.account = ACCOUNT;
    config.env.region = t.region;
    config.env.partition = t.partition;
    config.env.coreStackName = stackName;
    config.app.baseStackName = `t1-${name}`;
    config.app.adminUserId = "t1-admin";
    config.app.adminEmailAddress = "t1-admin@example.com";

    // Placeholders the templates ship with UNDEFINED that the ALB branch requires. Only filled when
    // that branch is actually enabled, so the commercial template is left as shipped.
    if ((config.app as any).useAlb?.enabled) {
        (config.app as any).useAlb.domainHost = "vams-t1.example.com";
        (
            config.app as any
        ).useAlb.certificateArn = `arn:${t.partition}:acm:${t.region}:${ACCOUNT}:certificate/11111111-2222-3333-4444-555555555555`;
        // A hosted zone would add a Route 53 record whose alias target differs per branch; not needed
        // for template assertions and it drags in a context lookup.
        (config.app as any).useAlb.optionalHostedZoneId = "";
    }

    // Internal Config fields that getConfig() normally derives. CDK Nag is left OFF: it is an aspect
    // that reports findings, and its suppression bookkeeping is not what this tier asserts.
    const internal = config as any;
    internal.enableCdkNag = false;
    internal.dockerDefaultPlatform = "linux/amd64";
    internal.openSearchAssetIndexName = "assets";
    internal.openSearchFileIndexName = "files";
    internal.resourceNamesSSMParamPrefix = `/${stackName}/resourceNames`;
    internal.openSearchDomainEndpointSSMParam = `/${stackName}/aos/endPoint`;
    internal.locationServiceApiKeyArnSSMParam = `/${stackName}/location/apiKeyArn`;
    internal.webUrlDeploymentSSMParam = `/${stackName}/web/deployedUrl`;

    mutate?.(internal);
    assertNoUntrackedDockerAsset(config);
    return config;
}

/**
 * Pipelines whose container image is built from a Dockerfile that is NOT in the repository, mapped to
 * the config flag that routes around the local build.
 *
 * `backendPipelines/3dRecon/splatToolbox/container/.gitignore` ignores `Dockerfile` under the heading
 * "Pipeline Source Download Ignore": the file arrives from an upstream sync and is absent from a fresh
 * checkout. Splat is the only one of the fifteen pipeline Dockerfiles that is untracked.
 *
 * That makes the failure CI-only, which is the worst shape it could have. `AssetImage.fromAsset` resolves
 * the path when the construct is built — before any bundling-skip logic runs, so stubbing Docker does not
 * help — and locally a previous sync has left the file behind, so the synth succeeds. The same test then
 * fails on a runner with `CannotFindFile`, pointing at `addContainer` rather than at the config that
 * asked for a local build.
 */
const UNTRACKED_DOCKER_ASSET_PIPELINES: Array<{
    flag: string;
    codeBuildFlag: string;
    dockerfile: string;
}> = [
    {
        flag: "useSplatToolbox",
        codeBuildFlag: "useCodeBuild",
        dockerfile: "backendPipelines/3dRecon/splatToolbox/container/Dockerfile",
    },
];

/**
 * Fail here, locally, rather than with `CannotFindFile` on a CI runner only.
 *
 * The check is on the CONFIG, not on whether the file happens to be present: a test that passes because
 * the developer synthesized this pipeline last week is exactly the trap being closed.
 */
function assertNoUntrackedDockerAsset(config: Config.Config): void {
    const pipelines = (config.app as any)?.pipelines ?? {};
    for (const { flag, codeBuildFlag, dockerfile } of UNTRACKED_DOCKER_ASSET_PIPELINES) {
        const entry = pipelines[flag];
        if (!entry?.enabled || entry[codeBuildFlag]) {
            continue;
        }
        throw new Error(
            `synthTemplate(): this config enables app.pipelines.${flag} with ${codeBuildFlag} false, ` +
                `which builds its image from ${dockerfile}. That file is gitignored and absent from a ` +
                `fresh checkout, so the synth succeeds locally and fails on CI with CannotFindFile. ` +
                `Set app.pipelines.${flag}.${codeBuildFlag} = true in the mutate — the image then ` +
                `resolves from the CodeBuild ECR repository and no local Dockerfile is read. Nothing ` +
                `about subnet, endpoint or Batch assertions changes: those key on ` +
                `${flag}.enabled alone.`
        );
    }
}

let dockerStubbed = false;

/**
 * Stub the eager `DockerImage.fromBuild` so no Docker daemon is required.
 *
 * Spying on the static is deliberate: `jest.mock("aws-cdk-lib")` also works but replaces the module
 * object, which breaks `instanceof` checks inside CDK itself.
 */
export function stubDockerBundling(): void {
    if (dockerStubbed) return;
    jest.spyOn(cdk.DockerImage, "fromBuild").mockImplementation(() =>
        cdk.DockerImage.fromRegistry("vams-t1-stub-bundling-image:latest")
    );
    dockerStubbed = true;
}

/**
 * Mark the Splat Toolbox container sources as already synced, so no synth clones over the network.
 *
 * `SplatToolboxConstruct`'s constructor syncs pinned upstream container sources into a SHARED temporary
 * directory and throws if the sync fails. Two things make that hostile to a test run: it needs the
 * network, and the directory is shared, so Jest workers building the construct concurrently collide —
 * observed as `EBUSY: resource busy or locked` while unlinking a git pack file, which surfaces as the
 * splat pipeline's resources being absent from the synth rather than as anything resembling a lock error.
 *
 * One suite already stubbed this for itself. It belongs here instead: every T1 synth of the commercial
 * template builds this construct, so a new suite that synthesizes with the pipeline enabled would
 * otherwise reintroduce the race by simply not knowing to opt out.
 */
function stubSplatToolboxSourceSync(): void {
    // Assigned through the class rather than mocked: the guard the constructor consults is this static,
    // and setting it is exactly what a completed sync would have done.
    (SplatToolboxConstruct as unknown as { syncedCommit?: string }).syncedCommit =
        SplatToolboxConstruct.GITHUB_REPO_COMMIT_HASH;
}

/**
 * Clear module-level state that survives between synths in one Jest module.
 *
 * `s3AssetBucketRecords` in `lib/helper/s3AssetBuckets.ts` is an exported mutable array with no reset,
 * so a second synth in the same process sees the first synth's buckets and dies with "There is already
 * a Construct with name 'bucketSyncCreated--<first stack name>--...'". The give-away is that the
 * duplicate name carries the PREVIOUS template's stack name.
 *
 * `RouteRegistry` and `ResourceNameRegistry` are classes instantiated per stack, so they do not leak.
 * Anything module-level added later must be reset here too, or the second template silently inherits
 * the first one's state.
 */
function resetGlobalRegistries(): void {
    s3AssetBucketRecords.length = 0;
}

const cache = new Map<string, SynthResult>();

/**
 * Synthesize one shipped config template and return every emitted resource.
 *
 * Results are cached per (template, mutation key) because a synth costs ~20 s. Pass `mutateKey` when
 * using `mutate`, or the cache will return a result built with different settings — a silent way to
 * assert against the wrong template.
 */
export function synthTemplate(
    name: TemplateName,
    opts: { mutate?: (c: any) => void; mutateKey?: string } = {}
): SynthResult {
    const key = `${name}::${opts.mutateKey ?? (opts.mutate ? "UNKEYED" : "")}`;
    if (opts.mutate && !opts.mutateKey) {
        throw new Error(
            "synthTemplate(): pass mutateKey alongside mutate, or two different mutations share a " +
                "cache entry and one of them silently asserts against the other's template."
        );
    }
    const hit = cache.get(key);
    if (hit) return hit;

    stubDockerBundling();
    stubSplatToolboxSourceSync();
    resetGlobalRegistries();
    const t = TARGET[name];
    const config = buildConfig(name, opts.mutate);
    const stackName = config.env.coreStackName;

    const app = newTestApp({
        context: {
            // The WHOLE cdk.json context, not a hand-picked subset. `cdk synth` applies every entry,
            // and several change the emitted template in ways a T1 assertion can trip over:
            //   @aws-cdk/aws-iam:minimizePolicies                     merges IAM statements
            //   @aws-cdk/aws-cloudfront:defaultSecurityPolicyTLSv1.2_2021  changes the CF TLS default
            //   @aws-cdk/core:stackRelativeExports                    changes export naming
            //   @aws-cdk/core:defaultCrossStackReferences             changes cross-stack refs
            // Picking out only `environments` (as this harness first did) left nine feature flags
            // unset, so the assembly under test differed from the one a real deploy produces — a test
            // could pass here and the behaviour still be wrong in the deployment, which is the one
            // failure mode a synth-assertion tier must not have.
            ...((cdkJson as any).context ?? {}),
            // Skips `docker run` for asset bundling. Necessary but NOT sufficient — see the header.
            // Set AFTER the spread so it cannot be overridden by a cdk.json entry.
            [cxapi.BUNDLING_STACKS]: [],
        },
    });

    Service.SetConfig(config);

    // Construct id MUST equal stackName — see the header note on Nag suppression paths.
    const stack = new Infra.CoreVAMSStack(app, stackName, {
        env: { account: ACCOUNT, region: t.region },
        stackName,
        ssmWafArnRegional: "",
        ssmWafArnCloudfront: "",
        config,
        description: `T1 synth assertion stack (${name})`,
    } as any);
    void stack;

    const asm = app.synth();
    const templates: Record<string, any> = {};
    const resources: Resource[] = [];
    for (const file of fs.readdirSync(asm.directory)) {
        if (!file.endsWith(".template.json")) continue;
        const stackKey = file.replace(/\.template\.json$/, "");
        const tpl = JSON.parse(fs.readFileSync(path.join(asm.directory, file), "utf8"));
        templates[stackKey] = tpl;
        for (const [logicalId, raw] of Object.entries<any>(tpl.Resources ?? {})) {
            resources.push({
                logicalId,
                stack: stackKey,
                type: raw.Type,
                properties: raw.Properties ?? {},
                raw,
            });
        }
    }

    if (resources.length === 0) {
        // A zero-resource result would satisfy every negative assertion in the suite.
        throw new Error(
            `synthTemplate(${name}) produced no resources across ${
                Object.keys(templates).length
            } ` + `template(s). Assertions built on this would pass vacuously.`
        );
    }

    // Annotations added by `Annotations.of(scope).addWarning*`. A construct inside a NESTED stack
    // reports on the enclosing top-level artifact, because a nested stack is not an artifact of its
    // own — so collecting from `asm.stacks` covers the whole app. These are what `cdk synth` and
    // `cdk deploy` print as "[Warning at /path]", and they appear in no *.template.json, so a check
    // that greps the templates cannot see them.
    const warnings: SynthWarning[] = asm.stacks.flatMap((artifact) =>
        artifact.messages
            .filter((m) => m.level === cxapi.SynthesisMessageLevel.WARNING)
            .map((m) => ({ path: m.id, message: String(m.entry.data) }))
    );

    // Remove the on-disk assembly now that templates, resources and warnings are all in memory.
    // A T1 assembly is ~166 MB and one jest run performs a dozen synths, so waiting for the
    // `newTestApp` teardown at the end of the file would hold every assembly of the file at once.
    // The app's outdir belongs to `newTestApp`, which removes whatever is left, so a synth that
    // throws before reaching here still leaks nothing.
    //
    // Best-effort by design: a failure to unlink must not fail a test that has already produced a
    // valid result, and on Windows an antivirus or indexer can hold a handle briefly.
    try {
        fs.rmSync(asm.directory, { recursive: true, force: true });
    } catch {
        /* leaves a temp directory behind; never a test failure */
    }

    const result = new SynthResult(name, t.partition, t.region, templates, resources, warnings);
    cache.set(key, result);
    return result;
}

/** All three shipped templates. Used by anything asserting a partition-wide property. */
export function synthAllTemplates(): Record<TemplateName, SynthResult> {
    return {
        commercial: synthTemplate("commercial"),
        govcloud: synthTemplate("govcloud"),
        eusovereign: synthTemplate("eusovereign"),
    };
}

/**
 * Assert something is absent, with a required control proving the check could have found it.
 *
 * `control` is not optional by design. An absent-resource assertion is satisfied equally by correct
 * behaviour and by a template that emitted nothing, and telling those apart is the whole point.
 *
 * ## Do NOT call this inside an `it.failing` test
 *
 * The safety mechanism inverts. `it.failing` requires the test body to throw, and this function throws
 * when the control finds nothing — so under `.failing`, a **broken control satisfies the ratchet** and
 * the test reports green while asserting nothing at all. That is the exact failure this helper exists to
 * prevent, reintroduced through the back door.
 *
 * The working pattern, which the T1 suites follow: put the control in its own ordinary (non-`.failing`)
 * test, and keep the `.failing` test to the post-fix assertion alone.
 *
 *     it("govcloud emits VPC endpoints at all", () => {           // control — must PASS today
 *         expect(synth("govcloud").countOfType("AWS::EC2::VPCEndpoint")).toBeGreaterThan(0);
 *     });
 *
 *     it.failing("FIX-###: no cognito endpoint is emitted", () => {   // ratchet — must FAIL today
 *         expect(cognitoEndpoints(synth("govcloud"))).toEqual([]);
 *     });
 */
export function expectAbsent(
    subject: string,
    found: unknown[],
    control: { description: string; count: number }
): void {
    // Jest's expect() takes a single argument, so the explanation goes in a thrown message rather than
    // a second parameter. Failing here is a harness/control problem, not a product problem, and the
    // message says so — otherwise the next reader "fixes" the wrong thing.
    if (control.count <= 0) {
        throw new Error(
            `positive control "${control.description}" found nothing, so the absence of ${subject} ` +
                `proves nothing. Fix the control before trusting this assertion.`
        );
    }
    expect(found).toEqual([]);
}

/** The restricted partitions, as a convenience for `test.each`. */
export const RESTRICTED_TEMPLATES: TemplateName[] = ["govcloud", "eusovereign"];
export const ALL_TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];
