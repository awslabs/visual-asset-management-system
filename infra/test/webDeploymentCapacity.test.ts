/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier — the /tmp budget of the CDK BucketDeployment Lambda that uploads the built web bundle.
 *
 * Covers S21-CUSTOMER-002: `cdk deploy` fails inside the website BucketDeployment custom resource once
 * `web/dist` outgrows the uploader Lambda's /tmp. That function downloads the bundle archive from the CDK
 * staging bucket into /tmp and expands it there, so it holds the archive AND the extracted tree at the same
 * time. /tmp defaults to 512 MiB and CDK emits no `EphemeralStorage` property at all when
 * `ephemeralStorageSize` is unset, so the default is invisible in the template — the budget can only be
 * asserted by finding the function the bucket-deployment custom resource actually invokes (through its
 * `ServiceToken`) and reading `EphemeralStorage.Size` off it.
 *
 * Two constructs deploy that same bundle and only ONE of them exists in any given configuration:
 * `CloudFrontS3WebSiteConstruct` when `useCloudFront.enabled`, `AlbS3WebsiteAlbDeployConstruct` when
 * `useAlb.enabled`. Each is asserted separately, because a test written as "some Lambda in the assembly
 * carries ephemeral storage" passes on whichever shape was fixed while the other keeps the default — which
 * is exactly how these two files drift.
 *
 * `config.template.commercial.json` ships `useAlb.enabled: false`, so reaching the ALB branch needs a keyed
 * hybrid config; `t1Distribution.test.ts` uses the same hybrid for the ALB TLS policy.
 */

import * as fs from "fs";
import * as path from "path";
import { Resource, SynthResult, expectAbsent, synthTemplate } from "./support/templateSynth";

// Two full-app synths at ~20 s each, each staging the whole web bundle as a CDK asset.
jest.setTimeout(600_000);

const LAMBDA = "AWS::Lambda::Function";
const LISTENER = "AWS::ElasticLoadBalancingV2::Listener";
const DISTRIBUTION = "AWS::CloudFront::Distribution";
const BUCKET_DEPLOYMENT = "Custom::CDKBucketDeployment";

/** Lambda's /tmp size when `ephemeralStorageSize` is not set, and the largest value it accepts. */
const DEFAULT_TMP_MIB = 512;
const MAX_TMP_MIB = 10240;

/**
 * The ALB branch built from the COMMERCIAL template. The shipped commercial config has
 * `useAlb.enabled: false`, so `AlbS3WebsiteAlbDeployConstruct` — and therefore its BucketDeployment — is
 * otherwise never synthesized from a shipped commercial config. `useGlobalVpc` comes up with it because the
 * ALB, its subnets and the S3 interface endpoint all need a VPC.
 */
const albHybrid = (c: any) => {
    c.app.useCloudFront.enabled = false;
    c.app.useAlb.enabled = true;
    c.app.useAlb.usePublicSubnet = false;
    c.app.useAlb.addAlbS3SpecialVpcEndpoint = true;
    c.app.useAlb.domainHost = "vams-t1-alb.example.com";
    c.app.useAlb.certificateArn =
        "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    c.app.useAlb.optionalHostedZoneId = "";
    c.app.useGlobalVpc.enabled = true;
};

const S = {
    /** As shipped: CloudFront on, ALB off. */
    cloudFront: () => synthTemplate("commercial"),
    /** Commercial partition + the ALB web branch. */
    alb: () => synthTemplate("commercial", { mutate: albHybrid, mutateKey: "web-deploy-alb" }),
};

// ---------------------------------------------------------------------------------------------
// Extraction helpers
// ---------------------------------------------------------------------------------------------

interface Deployment {
    /** The `Custom::CDKBucketDeployment` resource. */
    cr: Resource;
    /** The Lambda its ServiceToken points at — the function that unzips into /tmp. */
    handler: Resource;
    /** `EphemeralStorage.Size` in MiB, or undefined when the property is absent (the 512 MiB default). */
    tmpMiB: number | undefined;
}

/**
 * Every bucket deployment in the assembly, each paired with the Lambda it invokes.
 *
 * Resolved through `ServiceToken` rather than by matching a logical-id pattern: BucketDeployment builds a
 * SingletonFunction whose id encodes the memory limit and the ephemeral storage size, so the logical id
 * changes with the very property under test.
 */
function deployments(s: SynthResult): Deployment[] {
    return s.ofType(BUCKET_DEPLOYMENT).map((cr) => {
        const getAtt = cr.properties.ServiceToken?.["Fn::GetAtt"];
        if (!Array.isArray(getAtt)) {
            throw new Error(
                `${cr.stack}/${cr.logicalId}: ServiceToken is not an Fn::GetAtt, so the uploader ` +
                    `Lambda cannot be resolved: ${JSON.stringify(cr.properties.ServiceToken)}`
            );
        }
        const handlerId = getAtt[0];
        const handler = s.resources.find(
            (r) => r.stack === cr.stack && r.logicalId === handlerId && r.type === LAMBDA
        );
        if (!handler) {
            throw new Error(
                `${cr.stack}/${cr.logicalId}: no ${LAMBDA} named ${handlerId} in the same template`
            );
        }
        return { cr, handler, tmpMiB: handler.properties.EphemeralStorage?.Size };
    });
}

/** The bucket deployments that live in the StaticWeb nested stack, i.e. the web bundle uploads. */
function webDeployments(s: SynthResult): Deployment[] {
    return deployments(s).filter((d) => /StaticWeb/.test(d.cr.stack));
}

/**
 * The one web-bundle deployment in a synth.
 *
 * Throws rather than returning undefined when the count is wrong: a shape that emitted no BucketDeployment
 * at all must fail the /tmp assertions instead of satisfying them by absence.
 */
function webDeployment(s: SynthResult): Deployment {
    const found = webDeployments(s);
    if (found.length !== 1) {
        const total = s.countOfType(BUCKET_DEPLOYMENT);
        throw new Error(
            `expected exactly one web-bundle BucketDeployment in the StaticWeb stack, found ` +
                `${found.length} (${total} bucket deployments across the whole assembly). An ` +
                `assertion on its /tmp budget would prove nothing.`
        );
    }
    return found[0];
}

/** The logical id of the bucket a deployment writes to. */
const destinationBucketId = (d: Deployment): string =>
    SynthResult.flatten(d.cr.properties.DestinationBucketName);

/**
 * The uploader's declared /tmp size in MiB.
 *
 * An absent `EphemeralStorage` is the 512 MiB default, and it fails here with that named message rather
 * than as an `undefined` comparison in whichever matcher happened to read it.
 */
function tmpBudget(d: Deployment): number {
    if (typeof d.tmpMiB !== "number") {
        throw new Error(
            `${d.cr.stack}/${d.handler.logicalId} declares no EphemeralStorage, so the bucket ` +
                `deployment unzips under the ${DEFAULT_TMP_MIB} MiB Lambda default`
        );
    }
    return d.tmpMiB;
}

/** Bytes of the built web bundle the uploader has to stage — the footprint the budget is sized against. */
function webDistBytes(): number {
    const root = path.join(__dirname, "..", "..", "web", "dist");
    if (!fs.existsSync(root)) {
        throw new Error(
            `${root} does not exist. The synths in this suite stage it as a CDK asset, so they would ` +
                `have failed first — run the web build before this suite.`
        );
    }
    function walk(dir: string): number {
        let total = 0;
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            const p = path.join(dir, entry.name);
            if (entry.isDirectory()) total += walk(p);
            else if (entry.isFile()) total += fs.statSync(p).size;
        }
        return total;
    }
    return walk(root);
}

const MIB = 1024 * 1024;

// ---------------------------------------------------------------------------------------------
// Positive controls. These run first: they prove each shape emits the BucketDeployment under test and
// that it is the one uploading the web bundle, so no assertion below can pass on an absent construct.
// ---------------------------------------------------------------------------------------------

describe("harness controls: both web deployment shapes emit a bucket deployment", () => {
    test("the CloudFront shape emits one web bundle deployment, from the CloudFront construct", () => {
        const s = S.cloudFront();
        const d = webDeployment(s);
        expect(destinationBucketId(d)).toMatch(/WebAppBucket/);
        // `distribution`/`distributionPaths` are passed only by CloudFrontS3WebSiteConstruct, so this
        // identifies WHICH of the two constructs produced the deployment.
        expect(d.cr.properties.DistributionId).toBeDefined();
        expect(d.cr.properties.DistributionPaths).toEqual(["/*"]);
        expect(s.countOfType(DISTRIBUTION)).toBeGreaterThan(0);
        expect(s.countOfType(LISTENER)).toBe(0);
    });

    test("the ALB hybrid emits one web bundle deployment, from the ALB construct", () => {
        const s = S.alb();
        const d = webDeployment(s);
        expect(destinationBucketId(d)).toMatch(/WebAppBucket/);
        // The ALB construct passes no distribution, which is what makes this a different code path
        // rather than the same one synthesized twice.
        expect(d.cr.properties.DistributionId).toBeUndefined();
        expect(s.countOfType(DISTRIBUTION)).toBe(0);
        expect(s.countOfType(LISTENER)).toBeGreaterThan(0);
    });

    test("the resolved uploader is a real function and keeps its 1024 MiB memory limit", () => {
        // Property-path control: proves ServiceToken resolution landed on the bucket-deployment handler
        // rather than some other Lambda, and pins the memory dimension so a /tmp change cannot be made by
        // trading memory away.
        for (const d of [webDeployment(S.cloudFront()), webDeployment(S.alb())]) {
            expect(d.handler.properties.Handler).toBe("index.handler");
            expect(d.handler.properties.MemorySize).toBe(1024);
        }
    });
});

// ---------------------------------------------------------------------------------------------
// The /tmp budget itself.
// ---------------------------------------------------------------------------------------------

describe("the web bundle uploader gets more /tmp than the Lambda default", () => {
    it("CloudFront path: the BucketDeployment Lambda carries an ephemeral storage size above 512 MiB", () => {
        const tmpMiB = tmpBudget(webDeployment(S.cloudFront()));
        expect(tmpMiB).toBeGreaterThan(DEFAULT_TMP_MIB);
        expect(tmpMiB).toBeLessThanOrEqual(MAX_TMP_MIB);
    });

    it("ALB path: the BucketDeployment Lambda carries an ephemeral storage size above 512 MiB", () => {
        const tmpMiB = tmpBudget(webDeployment(S.alb()));
        expect(tmpMiB).toBeGreaterThan(DEFAULT_TMP_MIB);
        expect(tmpMiB).toBeLessThanOrEqual(MAX_TMP_MIB);
    });

    test("both constructs are sized identically", () => {
        // The two files are edited independently and deploy the same bundle, so a budget raised on one
        // path only would leave the other failing for the operators who use it.
        expect(tmpBudget(webDeployment(S.cloudFront()))).toBe(tmpBudget(webDeployment(S.alb())));
    });

    test("the budget still covers the archive plus its expansion for the current web/dist", () => {
        // The uploader holds the downloaded archive and the tree it expands to, so the floor is about
        // twice the bundle. This is the tripwire for growth: it fails when web/dist outgrows the budget
        // chosen against today's footprint, rather than letting the next viewer plugin fail a deployment.
        const distMiB = webDistBytes() / MIB;
        const tmpMiB = tmpBudget(webDeployment(S.cloudFront()));
        // eslint-disable-next-line no-console
        console.log(
            `[T1 web deploy] web/dist ${distMiB.toFixed(0)} MiB, uploader /tmp ${tmpMiB} MiB`
        );
        expect(distMiB).toBeGreaterThan(0);
        expect(tmpMiB).toBeGreaterThanOrEqual(2 * distMiB);
    });

    test("the budget is scoped to the web bundle — other bucket deployments keep the default", () => {
        // Scope check. The artefacts upload and the per-pipeline vamsSchema uploads move kilobytes, so
        // they need nothing; an aspect or a shared default applied stack-wide would show up here.
        const s = S.cloudFront();
        const others = deployments(s).filter((d) => !/StaticWeb/.test(d.cr.stack));
        const withEphemeralStorage = others
            .filter((d) => d.tmpMiB !== undefined)
            .map((d) => `${d.cr.stack}/${d.cr.logicalId}`);
        expectAbsent("ephemeral storage on a non-web bucket deployment", withEphemeralStorage, {
            description: "the assembly contains bucket deployments outside the StaticWeb stack",
            count: others.length,
        });
    });
});
