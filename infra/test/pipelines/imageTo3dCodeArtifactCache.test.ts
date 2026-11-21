/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Image to 3D CodeBuild path's AWS CodeArtifact PyPI cache exists only where CodeArtifact is
 * offered (some commercial Regions). Elsewhere, including AWS GovCloud (US) and the AWS European
 * Sovereign Cloud, an AWS::CodeArtifact::Domain fails the nested stack, so none is created, the
 * builds get empty CODEARTIFACT_* variables (the buildspec then writes a pip.conf that keeps the
 * public index) and the build role gets no CodeArtifact or bearer-token grant. Where the cache is
 * created it is encrypted with the deployment CMK when one is on, under a `-kms` name because a
 * domain's key is fixed at creation.
 */

import * as fs from "fs";
import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import {
    CODEARTIFACT_REGIONS,
    ImageTo3dCodeBuildConstruct,
    codeArtifactAvailable,
} from "../../lib/nestedStacks/pipelines/genAi/imageTo3d/constructs/imageTo3dCodeBuild-construct";
import { makePipelineHarness } from "../support/pipelineConstructHarness";

const BUILDSPEC = path.resolve(
    __dirname,
    "../../../backendPipelines/genAi/imageTo3d/container/buildspec.yml"
);

const synth = (id: string, partition: string, region: string, cmk: boolean): Template => {
    const h = makePipelineHarness(id, (c: Config.Config) => {
        c.env.partition = partition;
        c.env.region = region;
        c.app.pipelines.useImageTo3d.enabled = true;
        c.app.pipelines.useImageTo3d.useCodeBuild = true;
    });
    new ImageTo3dCodeBuildConstruct(h.stack, "ImageTo3dCodeBuild", {
        config: h.config,
        vpc: h.vpc,
        pipelineSubnets: h.subnets,
        pipelineSecurityGroups: h.securityGroups,
        kmsKey: cmk ? h.kmsKey : undefined,
    });
    return Template.fromStack(h.stack);
};

const buildEnvironment = (template: Template): Record<string, any> => {
    const projects = Object.values(template.findResources("AWS::CodeBuild::Project")) as any[];
    expect(projects.length).toBeGreaterThan(0);
    return Object.fromEntries(
        projects[0].Properties.Environment.EnvironmentVariables.map((v: any) => [v.Name, v.Value])
    );
};

const policyActions = (template: Template): string[] =>
    (Object.values(template.findResources("AWS::IAM::Policy")) as any[]).flatMap((p) =>
        p.Properties.PolicyDocument.Statement.flatMap((s: any) =>
            Array.isArray(s.Action) ? s.Action : [s.Action]
        )
    );

describe("codeArtifactAvailable", () => {
    test("only commercial Regions on the list", () => {
        expect(codeArtifactAvailable("aws", "us-east-1")).toBe(true);
        expect(codeArtifactAvailable("aws", "us-west-1")).toBe(false);
        expect(codeArtifactAvailable("aws", "ca-central-1")).toBe(false);
        expect(codeArtifactAvailable("aws-us-gov", "us-gov-west-1")).toBe(false);
        expect(codeArtifactAvailable("aws-eusc", "eusc-de-east-1")).toBe(false);
        expect(CODEARTIFACT_REGIONS.every((r) => !r.startsWith("us-gov-"))).toBe(true);
    });
});

describe.each([
    ["GovCloud", "aws-us-gov", "us-gov-west-1"],
    ["EU Sovereign Cloud", "aws-eusc", "eusc-de-east-1"],
    ["a commercial Region without CodeArtifact", "aws", "us-west-1"],
])("Image to 3D CodeBuild in %s", (label, partition, region) => {
    let template: Template;

    beforeAll(() => {
        template = synth(`ImageTo3dNoCodeArtifact${partition}${region}`, partition, region, false);
    });

    test("creates no CodeArtifact domain or repository", () => {
        expect(Object.keys(template.findResources("AWS::CodeArtifact::Domain"))).toHaveLength(0);
        expect(Object.keys(template.findResources("AWS::CodeArtifact::Repository"))).toHaveLength(
            0
        );
    });

    test("passes empty CodeArtifact variables and grants nothing for CodeArtifact", () => {
        const env = buildEnvironment(template);
        expect(env.CODEARTIFACT_DOMAIN).toBe("");
        expect(env.CODEARTIFACT_REPOSITORY).toBe("");
        const actions = policyActions(template);
        expect(actions.some((a) => a.startsWith("codeartifact:"))).toBe(false);
        expect(actions).not.toContain("sts:GetServiceBearerToken");
    });
});

describe("Image to 3D CodeBuild in a CodeArtifact Region", () => {
    test("creates the cache without a key when the CMK is off", () => {
        const template = synth("ImageTo3dCodeArtifactNoCmk", "aws", "us-east-1", false);
        const domains = Object.values(template.findResources("AWS::CodeArtifact::Domain")) as any[];
        expect(domains).toHaveLength(1);
        expect(domains[0].Properties.EncryptionKey).toBeUndefined();
        expect(domains[0].Properties.DomainName).not.toMatch(/-kms$/);
        expect(domains[0].Properties.DomainName.length).toBeLessThanOrEqual(50);
        expect(buildEnvironment(template).CODEARTIFACT_DOMAIN).toBeDefined();
        expect(policyActions(template)).toContain("codeartifact:ReadFromRepository");
    });

    test("encrypts the cache with the CMK under a -kms name when the CMK is on", () => {
        const template = synth("ImageTo3dCodeArtifactCmk", "aws", "us-east-1", true);
        const domains = Object.values(template.findResources("AWS::CodeArtifact::Domain")) as any[];
        expect(domains).toHaveLength(1);
        expect(JSON.stringify(domains[0].Properties.EncryptionKey)).toContain("Key");
        expect(domains[0].Properties.DomainName).toMatch(/-kms$/);
        expect(domains[0].Properties.DomainName.length).toBeLessThanOrEqual(50);
    });
});

describe("buildspec", () => {
    test("keeps the public index when no CodeArtifact domain is given", () => {
        const buildspec = fs.readFileSync(BUILDSPEC, "utf-8");
        // The token step runs under set -e, so it must not call CodeArtifact without a domain.
        expect(buildspec).toMatch(/if \[ -z "\$\{CODEARTIFACT_DOMAIN\}" \]; then/);
        expect(buildspec).toMatch(/printf '\[global\]\\n' > pip\.conf/);
    });
});
