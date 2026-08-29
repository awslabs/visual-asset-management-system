/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `app.pipelines.useRapidPipeline.useEks.eksClusterVersion` — the `getConfig()` half of the wiring.
 *
 * The construct builds the cluster with `eks.KubernetesVersion.of(<config value>)`, and
 * `KubernetesVersion.of()` accepts any string: `"v1.32"`, `"1.32.0"` and `"latest"` all synthesize
 * cleanly and then fail partway through creating the EKS control plane, rolling back the pipeline
 * nested stack. `getConfig()` therefore checks the shape so the failure is a configuration error
 * before anything is created.
 *
 * Only the shape is checked, not membership in a list of released minors: EKS adds a Kubernetes minor
 * roughly quarterly, and a hardcoded allow-list would make adopting a new one a code change. The
 * "1.99 is accepted" case below pins that decision, so relaxing or tightening it is a deliberate edit.
 *
 * `getConfig()` reads `config/config.json` from disk, so these tests mock `fs.readFileSync` to serve a
 * chosen template. Only the config filename is intercepted; every other read (the S3 policy and WAF
 * policy JSON that `getConfig()` also loads) falls through to the real implementation. The pattern —
 * including the warning against `jest.resetModules()` — is the one `configPartitionValidation.test.ts`
 * documents.
 *
 * The govcloud template is the base because enabling any pipeline that needs a VPC trips an earlier
 * `getConfig()` check (`app.useGlobalVpc.enabled must be true because ...`), and govcloud is the
 * shipped template that already has the global VPC on. The rule itself is partition-independent.
 */

import * as fs from "fs";
import * as Config from "../config/config";
import govcloudTemplate from "../config/config.template.govcloud.json";
import { newTestApp } from "./support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** Serve `configJson` for config.json; delegate every other path to the real fs. */
const serveConfig = (configJson: unknown) => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (path: string, ...rest: unknown[]) => {
            if (typeof path === "string" && path.endsWith("config.json")) {
                return JSON.stringify(configJson);
            }
            return realReadFileSync(path, ...rest);
        }
    );
};

// config.ts is imported once at module scope and getConfig() re-reads config.json on every call, so
// no jest.resetModules() is needed — and it must NOT be used. Resetting the registry re-runs the
// jest.mock("fs") factory, producing a SECOND mock instance: the freshly required config.ts binds to
// it while serveConfig() keeps configuring the original, so getConfig() silently reads the real
// on-disk config.json and every "does not throw" assertion passes vacuously.
const loadConfig = (mutate?: (c: any) => void) => {
    const config = JSON.parse(JSON.stringify(govcloudTemplate));
    config.env.region = "us-gov-west-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn =
            "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    }
    mutate?.(config);
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

/** The shipped template ships the pipeline disabled; every version case has to switch it on. */
const withEks = (eksClusterVersion: unknown) =>
    loadConfig((c) => {
        c.app.pipelines.useRapidPipeline.useEks.enabled = true;
        c.app.pipelines.useRapidPipeline.useEks.eksClusterVersion = eksClusterVersion;
    });

const VERSION_MESSAGE = /eksClusterVersion must be an Amazon EKS Kubernetes minor version/;

describe("useRapidPipeline.useEks.eksClusterVersion validation", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("the shipped 1.31 is accepted and survives getConfig() unchanged", () => {
        // Two claims in one: the rule does not reject the value every template ships, and getConfig()
        // does not default or normalize the field away before the construct reads it. Without the
        // second half, a rule could pass here while the resolved config still carried something else.
        // The second half is also what proves getConfig() completes — the negative is scoped to this
        // rule's message so an unrelated validation added elsewhere is not reported against it.
        const run = withEks("1.31");
        expect(run).not.toThrow(VERSION_MESSAGE);
        expect(run().app.pipelines.useRapidPipeline.useEks.eksClusterVersion).toBe("1.31");
    });

    test("an operator-set 1.32 survives getConfig() unchanged", () => {
        // The value the T1 synth assertion in t1StorageVpc.test.ts then follows into the emitted
        // Custom::AWSCDK-EKS-Cluster resource.
        const run = withEks("1.32");
        expect(run).not.toThrow(VERSION_MESSAGE);
        expect(run().app.pipelines.useRapidPipeline.useEks.eksClusterVersion).toBe("1.32");
    });

    test("a not-yet-released minor is accepted", () => {
        // Pins the deliberate choice NOT to validate against a list of known versions: adopting a new
        // EKS minor must be a config edit, not a code change.
        expect(withEks("1.99")).not.toThrow(VERSION_MESSAGE);
    });

    test.each([
        ["a leading v", "v1.32"],
        ["a patch component", "1.32.0"],
        ["a single-digit minor", "1.3"],
        ["a major-only version", "1"],
        ["a non-1.x major", "2.32"],
        ["a word", "latest"],
        ["an empty string", ""],
        ["surrounding whitespace", " 1.32 "],
        ["a number rather than a string", 1.32],
        ["null", null],
        ["undefined (a config predating the field)", undefined],
    ])("rejects %s", (_label, value) => {
        expect(withEks(value)).toThrow(VERSION_MESSAGE);
    });

    test("the rule is gated on useEks.enabled", () => {
        // NEGATIVE CONTROL for the table above. Every rejection there enables the pipeline, so without
        // this case a rule written to fire unconditionally would look identical — and it would break
        // the three shipped templates, which all declare the field with the pipeline switched off.
        const run = loadConfig((c) => {
            c.app.pipelines.useRapidPipeline.useEks.enabled = false;
            c.app.pipelines.useRapidPipeline.useEks.eksClusterVersion = "not-a-version";
        });
        expect(run).not.toThrow(VERSION_MESSAGE);
    });

    test("the shipped govcloud template passes as-is", () => {
        // Guards against the rule firing on an untouched shipped config.
        expect(loadConfig()).not.toThrow(VERSION_MESSAGE);
    });
});
