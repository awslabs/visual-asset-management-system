/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The RapidPipeline EKS timeout chain at the largest job timeout getConfig() accepts.
 *
 * Three clocks bound a run, each enforced by a different service, and they must nest:
 *
 *   longest run jobTimeout allows  <=  state machine TimeoutSeconds 21600  <  bundle taskTimeout "23400"
 *
 * The run is longer than `useEks.jobTimeout`. ConstructPipeline and RunJob run before the poll loop,
 * the poll ceiling is the job timeout plus a 60-second margin, every poll attempt runs CheckJob as well
 * as waiting, and PipelineEnd runs after the loop. A run that outlasts the state machine is stopped by
 * an execution-level States.Timeout, which bypasses every Catch, so PipelineEnd never reports it. The
 * parent workflow's pipeline task starts before the state machine does (vamsExecute and openPipeline
 * run first), so the bundle's taskTimeout must outlast the state machine: the parent is then still
 * waiting when PipelineEnd reports, and its own taskTimeout is what releases it if the state machine
 * times out.
 *
 * The template is synthesized with jobTimeout at its maximum, where the chain is tightest. The step
 * timeouts and the poll ceiling are read from the emitted definition and summed here, independently of
 * the arithmetic in config.ts; the getConfig() rules and their ConfigBuilder mirror are then checked at
 * the boundaries that arithmetic produces.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { SynthResult, synthTemplate } from "../support/templateSynth";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import {
    RULES,
    evaluateRules,
} from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

/**
 * `getConfig()` reads `config/config.json`, and the getConfig() tests below need it to read a chosen
 * configuration. A plain function rather than a `jest.fn`, because this file also synthesizes the
 * whole app and a mock would retain every path and Buffer CDK reads. The override lives on
 * `globalThis` because the factory is hoisted above this module's own initialization.
 */
const CONFIG_OVERRIDE_KEY = "__vamsEksTimeoutHeadroomConfigJson";
jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return {
        ...actual,
        readFileSync: (p: unknown, ...rest: unknown[]) => {
            const override = (globalThis as any).__vamsEksTimeoutHeadroomConfigJson;
            if (override !== undefined && typeof p === "string" && p.endsWith("config.json")) {
                return override;
            }
            return (actual.readFileSync as any)(p, ...rest);
        },
    };
});

const BUNDLE_PIPELINE_JSON = path.resolve(
    __dirname,
    "../../../backendPipelines/multi/rapidPipelineEKS/vamsSchema/pipeline.json"
);

const IMAGE_URI = "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";

/** The largest jobTimeout the arithmetic in config.ts lets fit in the state machine timeout. */
const LARGEST_FITTING_JOB_TIMEOUT = 15350;

function enableEksPipelineAtMaximum(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.enabled = true;
    c.app.pipelines.useRapidPipeline.autoRegisterWithVAMS = false;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI = IMAGE_URI;
    c.app.pipelines.useRapidPipeline.useEks.jobTimeout =
        Config.RAPID_PIPELINE_EKS_MAX_JOB_TIMEOUT_SECONDS;
}

/** The EKS state machine definition, its token references flattened to `${LogicalId...}`. */
function eksDefinition(synth: SynthResult): any {
    for (const template of Object.values<any>(synth.templates)) {
        for (const resource of Object.values<any>(template.Resources ?? {})) {
            if (resource.Type !== "AWS::StepFunctions::StateMachine") continue;
            const definition = SynthResult.flatten(resource.Properties?.DefinitionString);
            if (definition.includes('"RunJob"') && definition.includes('"InitializeCounter"')) {
                return JSON.parse(definition);
            }
        }
    }
    throw new Error("no emitted state machine carries the EKS RunJob state");
}

describe("RapidPipeline EKS timeout headroom", () => {
    let synth: SynthResult;
    let definition: any;
    let states: Record<string, any>;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipelineAtMaximum,
            mutateKey: "eks-timeout-headroom-at-maximum",
        });
        definition = eksDefinition(synth);
        states = definition.States;
    });

    /** The poll ceiling: the counter bound at which "Check Max Attempts" gives up on the job. */
    const pollCeiling = (): number => {
        const choice = states["Check Max Attempts"].Choices.find(
            (c: any) => c.Next === "Timeout Job"
        );
        return choice.NumericGreaterThanEquals;
    };

    test("[control] the EKS state machine is in this synth, built for the maximum jobTimeout", () => {
        // The pipeline ships disabled, so without this every assertion below could pass against a
        // template that contains none of these states.
        expect(synth.resources.filter((r) => r.type === "Custom::AWSCDK-EKS-Cluster").length).toBe(
            1
        );
        for (const name of ["ConstructPipeline", "RunJob", "CheckJob", "PipelineEnd"]) {
            expect(states[name]?.Type).toBe("Task");
        }
        expect(Config.RAPID_PIPELINE_EKS_MAX_JOB_TIMEOUT_SECONDS).toBe(14400);
        // ceil((14400 + 60) / 10): the ceiling this synth was built with, not the shipped 7200's.
        expect(pollCeiling()).toBe(1446);
        expect(states.InitializeCounter.Parameters.maxAttempts).toBe(1446);
    });

    test("the state machine timeout is the config constant", () => {
        expect(definition.TimeoutSeconds).toBe(21600);
        expect(definition.TimeoutSeconds).toBe(
            Config.RAPID_PIPELINE_EKS_STATE_MACHINE_TIMEOUT_SECONDS
        );
    });

    test("the bundle taskTimeout is the config constant and outlasts the state machine", () => {
        const bundle = JSON.parse(fs.readFileSync(BUNDLE_PIPELINE_JSON, "utf-8"));
        // A string in the bundle, as every shipped pipeline.json spells it.
        expect(bundle.executionConfig.taskTimeout).toBe("23400");
        const taskTimeout = Number(bundle.executionConfig.taskTimeout);
        expect(taskTimeout).toBe(Config.RAPID_PIPELINE_EKS_BUNDLE_TASK_TIMEOUT_SECONDS);
        // Strictly greater: the parent's task starts before the state machine does.
        expect(taskTimeout).toBeGreaterThan(definition.TimeoutSeconds);
        expect(bundle.executionConfig.waitForCallback).toBe("Enabled");
    });

    test("the poll loop runs CheckJob once more than it waits", () => {
        // The count the worst case below relies on. InitializeCounter goes straight to CheckJob with
        // the counter at 0; every later attempt increments the counter, waits and runs CheckJob again;
        // and the ceiling choice gives up once the counter reaches the ceiling. So a run that reaches
        // the ceiling has waited `ceiling` times and run CheckJob `ceiling + 1` times.
        expect(states.InitializeCounter.Parameters.counter).toBe(0);
        expect(states.InitializeCounter.Next).toBe("CheckJob");
        expect(states["Check Max Attempts"].Default).toBe("IncrementCounter");
        expect(states.IncrementCounter.Next).toBe("Wait 10 Seconds");
        expect(states["Wait 10 Seconds"].Next).toBe("CheckJob");
    });

    test("the emitted step timeouts and poll settings are the ones getConfig() counts", () => {
        // RunJob's and PipelineEnd's timeouts are set in the construct, not read from config.ts, so
        // this is what ties them to the budget getConfig() checks.
        expect(states.ConstructPipeline.TimeoutSeconds).toBeLessThanOrEqual(
            Config.RAPID_PIPELINE_EKS_CONSTRUCT_PIPELINE_TIMEOUT_SECONDS
        );
        expect(states.RunJob.TimeoutSeconds).toBeLessThanOrEqual(
            Config.RAPID_PIPELINE_EKS_RUN_JOB_TIMEOUT_SECONDS
        );
        for (const name of ["PipelineEnd", "PipelineEndSuccess"]) {
            expect(states[name].TimeoutSeconds).toBeLessThanOrEqual(
                Config.RAPID_PIPELINE_EKS_PIPELINE_END_TIMEOUT_SECONDS
            );
        }
        expect(states["Wait 10 Seconds"].Seconds).toBe(
            Config.RAPID_PIPELINE_EKS_JOB_CHECK_INTERVAL_SECONDS
        );
        expect(pollCeiling()).toBe(
            Config.rapidPipelineEksJobCheckAttempts(
                Config.RAPID_PIPELINE_EKS_MAX_JOB_TIMEOUT_SECONDS
            )
        );
    });

    test("the longest run at the maximum jobTimeout fits in the state machine timeout", () => {
        const ceiling = pollCeiling();
        const longestRun =
            states.ConstructPipeline.TimeoutSeconds +
            states.RunJob.TimeoutSeconds +
            ceiling * states["Wait 10 Seconds"].Seconds +
            (ceiling + 1) * Config.RAPID_PIPELINE_EKS_CHECK_JOB_ALLOWANCE_SECONDS +
            Math.max(states.PipelineEnd.TimeoutSeconds, states.PipelineEndSuccess.TimeoutSeconds);
        expect(longestRun).toBeLessThanOrEqual(definition.TimeoutSeconds);
        // getConfig() never counts less than the emitted template allows.
        expect(
            Config.rapidPipelineEksWorstCaseRunSeconds(
                Config.RAPID_PIPELINE_EKS_MAX_JOB_TIMEOUT_SECONDS
            )
        ).toBeGreaterThanOrEqual(longestRun);
    });

    test("the largest jobTimeout that fits is above the maximum", () => {
        // The gap between the two is the room the maximum leaves the run for Lambda retries.
        const worst = Config.rapidPipelineEksWorstCaseRunSeconds;
        const stateMachine = Config.RAPID_PIPELINE_EKS_STATE_MACHINE_TIMEOUT_SECONDS;
        expect(worst(LARGEST_FITTING_JOB_TIMEOUT)).toBeLessThanOrEqual(stateMachine);
        expect(worst(LARGEST_FITTING_JOB_TIMEOUT + 1)).toBeGreaterThan(stateMachine);
        expect(LARGEST_FITTING_JOB_TIMEOUT).toBeGreaterThan(
            Config.RAPID_PIPELINE_EKS_MAX_JOB_TIMEOUT_SECONDS
        );
    });
});

describe("getConfig() bounds jobTimeout by the state machine timeout and the maximum", () => {
    afterEach(() => {
        delete (globalThis as any)[CONFIG_OVERRIDE_KEY];
    });

    const withJobTimeout = (jobTimeout: unknown) => {
        const config = JSON.parse(JSON.stringify(commercialTemplate));
        config.env.region = "us-west-2";
        config.env.account = "123456789012";
        config.app.baseStackName = "vamstest";
        config.app.useGlobalVpc.enabled = true;
        config.app.pipelines.useRapidPipeline.enabled = true;
        config.app.pipelines.useRapidPipeline.useEks.enabled = true;
        config.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI = IMAGE_URI;
        config.app.pipelines.useRapidPipeline.useEks.jobTimeout = jobTimeout;
        (globalThis as any)[CONFIG_OVERRIDE_KEY] = JSON.stringify(config);
        return () => Config.getConfig(newTestApp());
    };

    test("the shipped value and the maximum are accepted", () => {
        expect(withJobTimeout(7200)).not.toThrow();
        expect(withJobTimeout(14400)).not.toThrow();
    });

    test("a jobTimeout whose run cannot fit in the state machine timeout is rejected", () => {
        expect(withJobTimeout(LARGEST_FITTING_JOB_TIMEOUT + 1)).toThrow(
            /pipelines\.useRapidPipeline\.useEks\.jobTimeout \(15351s\) lets a pipeline run take up to \d+s, longer than the 21600s timeout of the pipeline's state machine/
        );
    });

    test("a jobTimeout above the maximum is rejected, even where the run would fit", () => {
        for (const jobTimeout of [14401, LARGEST_FITTING_JOB_TIMEOUT]) {
            expect(withJobTimeout(jobTimeout)).toThrow(
                new RegExp(
                    `pipelines\\.useRapidPipeline\\.useEks\\.jobTimeout \\(${jobTimeout}s\\) exceeds the 14400s maximum`
                )
            );
        }
    });
});

describe("ConfigBuilder validation.ts mirrors the getConfig() jobTimeout rules", () => {
    const RULE_IDS = [
        "rapidpipeline-eks-job-timeout",
        "rapidpipeline-eks-job-timeout-exceeds-state-machine-timeout",
        "rapidpipeline-eks-job-timeout-exceeds-maximum",
    ];

    /** The ids of the jobTimeout rules that fire on the commercial preset with this jobTimeout. */
    const firing = (jobTimeout: unknown): string[] => {
        const config = makeDefaultConfig("commercial");
        config.app.useGlobalVpc.enabled = true;
        config.app.pipelines.useRapidPipeline.enabled = true;
        config.app.pipelines.useRapidPipeline.useEks.enabled = true;
        config.app.pipelines.useRapidPipeline.useEks.jobTimeout = jobTimeout;
        return evaluateRules(config)
            .map((rule) => rule.id)
            .filter((id) => RULE_IDS.includes(id));
    };

    test("[control] every rule id this test names exists exactly once", () => {
        const ids = RULES.map((rule) => rule.id);
        for (const id of RULE_IDS) {
            expect(ids.filter((candidate) => candidate === id)).toHaveLength(1);
        }
    });

    test("the shipped value and the maximum raise none of them", () => {
        expect(firing(7200)).toEqual([]);
        expect(firing(14400)).toEqual([]);
    });

    test("the rules fire at the same boundaries as getConfig()", () => {
        expect(firing(14401)).toEqual(["rapidpipeline-eks-job-timeout-exceeds-maximum"]);
        expect(firing(LARGEST_FITTING_JOB_TIMEOUT)).toEqual([
            "rapidpipeline-eks-job-timeout-exceeds-maximum",
        ]);
        expect(firing(LARGEST_FITTING_JOB_TIMEOUT + 1).sort()).toEqual([
            "rapidpipeline-eks-job-timeout-exceeds-maximum",
            "rapidpipeline-eks-job-timeout-exceeds-state-machine-timeout",
        ]);
        expect(firing(0)).toEqual(["rapidpipeline-eks-job-timeout"]);
    });
});
