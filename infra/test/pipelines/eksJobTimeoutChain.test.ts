/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The RapidPipeline EKS job timeout is one value in a three-link chain, and every link must agree.
 *
 *   poll ceiling (Step Functions)  >  pod deadline (activeDeadlineSeconds)  <=  bundle taskTimeout
 *
 * Each link is enforced in a different place, which is why a mismatch produced a wrong OUTCOME rather
 * than an error:
 *
 *   * The poll ceiling was a hardcoded 360 attempts at 10-second intervals — 60 minutes.
 *   * The pod deadline was a hardcoded 7200 seconds — 2 hours.
 *   * The registered bundle declares taskTimeout 14400 — 4 hours.
 *   * `useEks.jobTimeout`, the configuration field that exists for exactly this, was read by NOTHING.
 *
 * A job running between 60 and 120 minutes was therefore reported FAILED to the parent workflow through
 * `SendTaskFailure` while its pod carried on for up to another hour and kept writing output — an
 * execution that says it failed and then produces results.
 *
 * The relation is a strict inequality on the first link, not an equality. If the poll ceiling merely
 * equalled the pod deadline the two clocks would expire together, so the poll would give up at the same
 * instant Kubernetes terminated the pod and the outcome would again be reported as a poll timeout rather
 * than as the pod's own failure. That is the same trap as the Cosmos 3 state-machine timeout
 * (S33-CDK-001), reached from the other direction.
 *
 * Asserted on the emitted state machine definition and the emitted Lambda environment rather than on the
 * TypeScript, because the derivation is arithmetic and an off-by-one in it is invisible in source.
 */

import * as path from "path";
import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { SynthResult, synthTemplate } from "../support/templateSynth";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const serveConfig = (configJson: unknown) => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (filePath: string, ...rest: unknown[]) => {
            if (typeof filePath === "string" && filePath.endsWith("config.json")) {
                return JSON.stringify(configJson);
            }
            return realReadFileSync(filePath, ...rest);
        }
    );
};

/** The interval the construct polls at; the ceiling is expressed in attempts of this length. */
const POLL_INTERVAL_SECONDS = 10;

function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.enabled = true;
    c.app.pipelines.useRapidPipeline.autoRegisterWithVAMS = false;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

describe("EKS job timeout chain", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-timeout-chain",
        });
    });

    afterEach(() => {
        // Restored to delegating rather than cleared, or the synthTemplate harness in any later test
        // reads nothing instead of failing outright.
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    });

    /** The configured value every link is supposed to derive from. */
    const configuredTimeout = () =>
        (commercialTemplate as any).app.pipelines.useRapidPipeline.useEks.jobTimeout as number;

    test("the pipeline IS in this synth", () => {
        // The control: the EKS pipeline ships disabled, so without enabling it every assertion below
        // would pass against a template containing none of these resources.
        expect(synth.resources.filter((r) => r.type === "Custom::AWSCDK-EKS-Cluster").length).toBe(
            1
        );
        expect(configuredTimeout()).toBeGreaterThan(0);
    });

    test("the state machine's poll ceiling STRICTLY exceeds the configured job timeout", () => {
        // Read out of the emitted ASL, where the ceiling appears as the numeric bound the counter is
        // compared against — the arithmetic is what matters, not the source expression.
        const definitions = Object.values(synth.templates)
            .flatMap((t: any) => Object.values(t.Resources ?? {}))
            .filter((r: any) => r.Type === "AWS::StepFunctions::StateMachine")
            .map((r: any) => JSON.stringify(r.Properties?.DefinitionString ?? ""));

        const eksDefinition = definitions.find(
            (d) => d.includes("CheckJobStatus") || d.includes("counter")
        );
        expect(eksDefinition).toBeDefined();

        // The ceiling is the largest counter bound in the definition.
        const bounds = [...eksDefinition!.matchAll(/NumericGreaterThanEquals\\?":\s*(\d+)/g)].map(
            (m) => Number(m[1])
        );
        expect(bounds.length).toBeGreaterThan(0);
        const ceilingSeconds = Math.max(...bounds) * POLL_INTERVAL_SECONDS;

        expect(ceilingSeconds).toBeGreaterThan(configuredTimeout());
    });

    test("the pod deadline is passed to the handler from the same configured value", () => {
        // Not hardcoded in the handler any more. If this env var were absent the handler would fall back
        // to its own default and the two links could drift apart again silently.
        const handlers = synth
            .ofType("AWS::Lambda::Function")
            .filter(
                (f) => "EKS_JOB_TIMEOUT_SECONDS" in (f.properties.Environment?.Variables ?? {})
            );
        expect(handlers.length).toBeGreaterThan(0);
        for (const handler of handlers) {
            expect(Number(handler.properties.Environment.Variables.EKS_JOB_TIMEOUT_SECONDS)).toBe(
                configuredTimeout()
            );
        }
    });

    test("the handler uses that variable rather than a literal deadline", () => {
        const source = realReadFileSync(
            path.resolve(
                __dirname,
                "../../../backendPipelines/multi/rapidPipelineEKS/lambda/consolidated_handler.py"
            ),
            "utf-8"
        );
        expect(source).toContain('"activeDeadlineSeconds": JOB_TIMEOUT_SECONDS');
        expect(source).toContain("EKS_JOB_TIMEOUT_SECONDS");
    });

    test("the configured timeout does not exceed the bundle's taskTimeout", () => {
        // The third link. The parent workflow stops waiting for the callback at taskTimeout, so a pod
        // allowed to run longer leaves the parent giving up on a job that is still going.
        const bundle = JSON.parse(
            realReadFileSync(
                path.resolve(
                    __dirname,
                    "../../../backendPipelines/multi/rapidPipelineEKS/vamsSchema/pipeline.json"
                ),
                "utf-8"
            )
        );
        const taskTimeout = Number(bundle.executionConfig.taskTimeout);
        expect(taskTimeout).toBe(Config.RAPID_PIPELINE_EKS_BUNDLE_TASK_TIMEOUT_SECONDS);
        expect(configuredTimeout()).toBeLessThanOrEqual(taskTimeout);
    });

    describe("getConfig() rejects a timeout that would break the chain", () => {
        const withJobTimeout = (jobTimeout: unknown) => {
            const config = JSON.parse(JSON.stringify(commercialTemplate));
            config.env.region = "us-west-2";
            config.env.account = "123456789012";
            config.app.baseStackName = "vamstest";
            config.app.useGlobalVpc.enabled = true;
            config.app.pipelines.useRapidPipeline.enabled = true;
            config.app.pipelines.useRapidPipeline.useEks.enabled = true;
            config.app.pipelines.useRapidPipeline.useEks.jobTimeout = jobTimeout;
            serveConfig(config);
            return () => Config.getConfig(newTestApp());
        };

        test("the shipped value is accepted", () => {
            // Positive control: the rules must not reject what every template ships.
            expect(withJobTimeout(configuredTimeout())).not.toThrow(/jobTimeout/);
        });

        test("a timeout beyond the bundle taskTimeout is rejected and names both", () => {
            expect(
                withJobTimeout(Config.RAPID_PIPELINE_EKS_BUNDLE_TASK_TIMEOUT_SECONDS + 1)
            ).toThrow(/exceeds the \d+s taskTimeout/);
        });

        test("exactly the bundle taskTimeout is accepted", () => {
            // The boundary. Equal is fine for this link: the parent waits at least as long as the pod
            // may run.
            expect(
                withJobTimeout(Config.RAPID_PIPELINE_EKS_BUNDLE_TASK_TIMEOUT_SECONDS)
            ).not.toThrow(/jobTimeout/);
        });

        test("a non-positive or non-integer timeout is rejected", () => {
            expect(withJobTimeout(0)).toThrow(/must be a positive integer/);
            expect(withJobTimeout(-1)).toThrow(/must be a positive integer/);
            expect(withJobTimeout(1.5)).toThrow(/must be a positive integer/);
            expect(withJobTimeout("7200")).toThrow(/must be a positive integer/);
        });

        test("the rules do not fire when the EKS pipeline is disabled", () => {
            // Backwards compatibility: a deployment not using the pipeline must not be blocked by a
            // value it never consumes.
            const config = JSON.parse(JSON.stringify(commercialTemplate));
            config.env.region = "us-west-2";
            config.env.account = "123456789012";
            config.app.baseStackName = "vamstest";
            config.app.pipelines.useRapidPipeline.useEks.enabled = false;
            config.app.pipelines.useRapidPipeline.useEks.jobTimeout = 999999;
            serveConfig(config);
            expect(() => Config.getConfig(newTestApp())).not.toThrow(/jobTimeout/);
        });
    });
});
