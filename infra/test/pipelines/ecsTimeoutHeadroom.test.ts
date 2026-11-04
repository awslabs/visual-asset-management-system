/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The RapidPipeline (Amazon ECS) timeout chain. Three clocks bound a run, each enforced by a different
 * service, and they must nest:
 *
 *   ConstructPipeline + Fargate task TimeoutSeconds 17040 + stop route + PipelineEnd
 *     <=  state machine TimeoutSeconds 18000  <  bundle taskTimeout "19800"
 *
 * The Fargate task is the only step without a bound of its own: ConstructPipeline and PipelineEnd are
 * Lambda invocations, bounded by their functions' timeouts, while a `.sync` ECS RunTask waits for the
 * task to stop however long it runs. A run that outlasts the state machine is stopped by an
 * execution-level States.Timeout, which bypasses every Catch, so PipelineEnd never reports it and the
 * parent workflow is released only by its own taskTimeout. A task-level States.Timeout is a state
 * error: the Fargate task's Catch takes it through the stop route (which stops the task, best-effort)
 * to PipelineEnd, which reports the failure to the parent. The stop route runs only on that timeout,
 * so it sits in the longest run: its calls' TimeoutSeconds, retries and backoff are counted here.
 * The parent workflow's pipeline task starts before the state machine does (vamsExecute invokes
 * openPipeline synchronously, and openPipeline starts the execution), so the bundle's taskTimeout must
 * outlast the state machine: the parent is then still waiting when PipelineEnd reports.
 *
 * The step bounds are read from the synthesized template (the state's TimeoutSeconds, and the Lambda
 * function's Timeout behind each LambdaInvoke state) and summed here, independently of config.ts.
 */

import * as fs from "fs";
import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import { RapidPipelineConstruct } from "../../lib/nestedStacks/pipelines/multi/rapidPipeline/constructs/rapidPipeline-construct";
import { ACCOUNT, REGION, makePipelineHarness } from "../support/pipelineConstructHarness";

const BUNDLE_PIPELINE_JSON = path.resolve(
    __dirname,
    "../../../backendPipelines/multi/rapidPipeline/vamsSchema/pipeline.json"
);

const IMAGE_URI = `${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/rapidpipeline:latest`;

const FARGATE = "RapidPipelineRunFargate";
const CONSTRUCT = "ConstructPipelineTask";
const END = "PipelineEndTask";

/** A CloudFormation value flattened to a string, tokens kept as `${LogicalId}` / `${LogicalId.Attr}`. */
const flatten = (value: any): string => {
    if (typeof value === "string") return value;
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.map(flatten).join("");
    if (typeof value === "object") {
        if (value["Fn::Join"]) {
            const [separator, parts] = value["Fn::Join"];
            return (parts as any[]).map(flatten).join(separator);
        }
        if (value.Ref) return `\${${value.Ref}}`;
        if (value["Fn::GetAtt"]) return `\${${(value["Fn::GetAtt"] as any[]).join(".")}}`;
        return Object.values(value).map(flatten).join("");
    }
    return String(value);
};

/** Worst-case backoff waits of a state's retriers: each waits Interval * Backoff^k before attempt k+1. */
const retryBackoffSeconds = (state: any): number =>
    (state.Retry ?? []).reduce((total: number, retrier: any) => {
        const interval = retrier.IntervalSeconds ?? 1;
        const attempts = retrier.MaxAttempts ?? 3;
        const backoff = retrier.BackoffRate ?? 2;
        let waits = 0;
        for (let k = 0; k < attempts; k++) {
            const wait = interval * Math.pow(backoff, k);
            waits += retrier.MaxDelaySeconds ? Math.min(wait, retrier.MaxDelaySeconds) : wait;
        }
        return total + waits;
    }, 0);

/** Whether a catcher's or retrier's ErrorEquals matches a task-level States.Timeout. */
const matchesTimeout = (errorEquals: string[]): boolean =>
    // States.TaskFailed matches every error except States.Timeout, so it does not count here.
    errorEquals.includes("States.ALL") || errorEquals.includes("States.Timeout");

/**
 * The longest a bounded Task state can take: every attempt runs to its TimeoutSeconds, and each
 * retrier's attempts and backoff waits are used up in turn.
 */
const taskWorstCaseSeconds = (state: any): number => {
    expect(typeof state.TimeoutSeconds).toBe("number");
    const retries = (state.Retry ?? []).reduce(
        (total: number, retrier: any) => total + (retrier.MaxAttempts ?? 3),
        0
    );
    return (1 + retries) * state.TimeoutSeconds + retryBackoffSeconds(state);
};

describe("RapidPipeline ECS timeout headroom", () => {
    let template: Template;
    let definition: any;
    let states: Record<string, any>;
    let stateMachine: any;

    beforeAll(() => {
        const h = makePipelineHarness("RapidPipelineEcsTimeoutStack", (c) => {
            c.app.pipelines.useRapidPipeline.useEcs.enabled = true;
            c.app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI = IMAGE_URI;
            c.app.pipelines.useRapidPipeline.useEcs.autoRegisterWithVAMS = false;
        });
        const construct = new RapidPipelineConstruct(h.stack, "RapidPipelineConstruct", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnetsPrivate: h.subnets,
            pipelineSubnetsIsolated: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        template = Template.fromStack(construct);
        const machines = Object.values<any>(
            template.findResources("AWS::StepFunctions::StateMachine")
        );
        expect(machines).toHaveLength(1);
        stateMachine = machines[0];
        definition = JSON.parse(flatten(stateMachine.Properties.DefinitionString));
        states = definition.States;
    });

    /** The Lambda function a LambdaInvoke state calls, as its template resource. */
    const lambdaBehind = (stateName: string): any => {
        const functionName = flatten(states[stateName].Parameters.FunctionName);
        const match = /^\$\{([A-Za-z0-9]+)\.Arn\}$/.exec(functionName);
        expect(match).not.toBeNull();
        const fn = template.findResources("AWS::Lambda::Function")[match![1]];
        expect(fn).toBeDefined();
        return fn;
    };

    /**
     * How long a LambdaInvoke state can run without retries: the function's own timeout, or the
     * state's TimeoutSeconds when that is shorter.
     */
    const lambdaStateBound = (stateName: string): number => {
        const functionTimeout = lambdaBehind(stateName).Properties.Timeout;
        expect(typeof functionTimeout).toBe("number");
        const stateTimeout = states[stateName].TimeoutSeconds;
        return stateTimeout === undefined
            ? functionTimeout
            : Math.min(stateTimeout, functionTimeout);
    };

    /** The first Fargate catcher that matches States.Timeout: catchers are evaluated in order. */
    const timeoutCatcher = (): any =>
        (states[FARGATE].Catch ?? []).find((c: any) => matchesTimeout(c.ErrorEquals));

    /**
     * The longest path from `name` to PipelineEnd (or to the end of a Map iteration), in seconds.
     * A Task counts its own worst case and then the longer of its Next and its catch targets, since
     * a failure takes no longer than the attempts it used up. A Map's iterations run concurrently, so
     * it counts one iteration. Choice and Pass states take no time of their own.
     */
    const routeWorstCaseSeconds = (
        name: string,
        scope: Record<string, any>,
        visited: string[] = []
    ): number => {
        if (scope === states && name === END) return 0;
        expect(visited).not.toContain(name);
        const state = scope[name];
        expect(state).toBeDefined();
        let own = 0;
        if (state.Type === "Task") {
            own = taskWorstCaseSeconds(state);
        } else if (state.Type === "Map") {
            own = routeWorstCaseSeconds(state.ItemProcessor.StartAt, state.ItemProcessor.States);
        } else {
            expect(["Choice", "Pass"]).toContain(state.Type);
        }
        const next: string[] = [
            ...(state.Next ? [state.Next] : []),
            ...(state.Choices ?? []).map((c: any) => c.Next),
            ...(state.Default ? [state.Default] : []),
            ...(state.Catch ?? []).map((c: any) => c.Next),
        ];
        // Only a state that ends a Map iteration may have nowhere to go.
        if (next.length === 0) {
            expect(scope).not.toBe(states);
            expect(state.End).toBe(true);
        }
        const after = next.map((n) => routeWorstCaseSeconds(n, scope, [...visited, name]));
        return own + Math.max(0, ...after);
    };

    test("[control] the ECS state machine is in this template", () => {
        expect(definition.StartAt).toBe(CONSTRUCT);
        expect(states[CONSTRUCT].Next).toBe(FARGATE);
        expect(states[FARGATE].Type).toBe("Task");
        // The .sync pattern: the state waits for the task to stop, which is what needs the bound.
        expect(flatten(states[FARGATE].Resource)).toMatch(/:states:::ecs:runTask\.sync$/);
        expect(states[FARGATE].Next).toBe(END);
        expect(lambdaBehind(CONSTRUCT).Properties.Handler).toBe("constructPipeline.lambda_handler");
        expect(lambdaBehind(END).Properties.Handler).toBe("pipelineEnd.lambda_handler");
    });

    test("the state machine timeout is the config constant", () => {
        expect(definition.TimeoutSeconds).toBe(18000);
        expect(definition.TimeoutSeconds).toBe(
            Config.RAPID_PIPELINE_ECS_STATE_MACHINE_TIMEOUT_SECONDS
        );
    });

    test("the Fargate task carries a task-level timeout, the config constant", () => {
        expect(states[FARGATE].TimeoutSeconds).toBe(17040);
        expect(states[FARGATE].TimeoutSeconds).toBe(
            Config.RAPID_PIPELINE_ECS_FARGATE_TASK_TIMEOUT_SECONDS
        );
    });

    test("the Lambda steps are bounded by the budget config.ts counts for them", () => {
        expect(lambdaStateBound(CONSTRUCT)).toBeLessThanOrEqual(
            Config.RAPID_PIPELINE_ECS_CONSTRUCT_PIPELINE_TIMEOUT_SECONDS
        );
        expect(lambdaStateBound(END)).toBeLessThanOrEqual(
            Config.RAPID_PIPELINE_ECS_PIPELINE_END_TIMEOUT_SECONDS
        );
    });

    test("the longest run fits in the state machine timeout, with room for the Lambda retries", () => {
        const fargate = states[FARGATE].TimeoutSeconds;
        expect(typeof fargate).toBe("number");
        // A timed-out task goes through the stop route before PipelineEnd. Its calls are bounded,
        // and the route is what a run that used its whole task bound takes, so it is counted.
        const catcher = timeoutCatcher();
        expect(catcher).toBeDefined();
        const stopRoute = routeWorstCaseSeconds(catcher.Next, states);
        // ListTasks, DescribeTasks and one concurrent round of StopTask, 18 s each at worst: the
        // stop-route worst case config.ts counts.
        expect(stopRoute).toBe(54);
        const longestRun =
            lambdaStateBound(CONSTRUCT) + fargate + stopRoute + lambdaStateBound(END);
        expect(longestRun).toBeLessThanOrEqual(definition.TimeoutSeconds);
        // The room left covers the backoff waits of the Lambda states' service-exception retries.
        const retryWaits =
            retryBackoffSeconds(states[CONSTRUCT]) + retryBackoffSeconds(states[END]);
        expect(retryWaits).toBeGreaterThan(0);
        expect(definition.TimeoutSeconds - longestRun).toBeGreaterThanOrEqual(retryWaits);
    });

    test("a timed-out Fargate task is caught, not retried, and reaches PipelineEnd with the error", () => {
        const fargate = states[FARGATE];
        // A retrier matching States.Timeout would start the task again past its budget.
        for (const retrier of fargate.Retry ?? []) {
            expect(matchesTimeout(retrier.ErrorEquals)).toBe(false);
        }
        // Catchers are evaluated in order; the first that matches States.Timeout is the route.
        const catcher = timeoutCatcher();
        expect(catcher).toBeDefined();
        // pipelineEnd reads the token from the state input and the error from $.error.
        expect(catcher.ResultPath).toBe("$.error");
        // Every path from the catch to PipelineEnd must leave the input and $.error as they are, so
        // pipelineEnd still finds the token and reports the timeout. A state on the way may add its
        // own result beside them, or discard it.
        const keepsInputAndError = (resultPath: unknown): boolean =>
            resultPath === null ||
            (typeof resultPath === "string" &&
                resultPath.startsWith("$.") &&
                resultPath !== "$.error" &&
                !resultPath.startsWith("$.error."));
        // A JSONata state has no ResultPath. Its Output, and each of its catchers', must merge one
        // field other than `error` into the state input: `$merge([$states.input, {'<field>': ...}])`.
        const jsonataKeepsInputAndError = (output: unknown): boolean => {
            if (typeof output !== "string") return false;
            const merged =
                /^\{% \$merge\(\[\$states\.input, \{'([A-Za-z][A-Za-z0-9]*)': .+\}\]\) %\}$/s.exec(
                    output
                );
            return merged !== null && merged[1] !== "error";
        };
        const reachedEnd: string[] = [];
        const walk = (name: string, visited: string[]): void => {
            if (name === END) {
                reachedEnd.push(visited[visited.length - 1] ?? "(catch)");
                return;
            }
            expect(visited).not.toContain(name);
            const state = states[name];
            switch (state.Type) {
                case "Pass":
                    // A pass-through that leaves the input as it is.
                    expect(state.ResultPath ?? "$").toBe("$");
                    expect(state.Result).toBeUndefined();
                    expect(state.Parameters).toBeUndefined();
                    break;
                case "Task":
                case "Map":
                    if (state.QueryLanguage === "JSONata") {
                        expect(jsonataKeepsInputAndError(state.Output)).toBe(true);
                        expect(state.Assign).toBeUndefined();
                        for (const c of state.Catch ?? []) {
                            expect(jsonataKeepsInputAndError(c.Output)).toBe(true);
                        }
                        break;
                    }
                    expect(state.InputPath ?? "$").toBe("$");
                    expect(state.OutputPath ?? "$").toBe("$");
                    expect(keepsInputAndError(state.ResultPath)).toBe(true);
                    for (const c of state.Catch ?? []) {
                        expect(keepsInputAndError(c.ResultPath)).toBe(true);
                    }
                    break;
                case "Choice":
                    break;
                default:
                    throw new Error(`unexpected ${state.Type} state ${name} on the timeout route`);
            }
            const next: string[] = [
                ...(state.Next ? [state.Next] : []),
                ...(state.Choices ?? []).map((c: any) => c.Next),
                ...(state.Default ? [state.Default] : []),
                ...(state.Catch ?? []).map((c: any) => c.Next),
            ];
            expect(next.length).toBeGreaterThan(0);
            for (const n of next) walk(n, [...visited, name]);
        };
        walk(catcher.Next, []);
        expect(reachedEnd.length).toBeGreaterThan(0);
        // PipelineEnd passes its whole input to the handler, which sends SendTaskFailure when the
        // input carries `error`; the run then ends in the Fail state.
        expect(states[END].Parameters["Payload.$"]).toBe("$");
        const choice = states[states[END].Next];
        expect(choice.Type).toBe("Choice");
        const onError = choice.Choices.find(
            (c: any) => c.Variable === "$.error" && c.IsPresent === true
        );
        expect(onError).toBeDefined();
        expect(states[onError.Next].Type).toBe("Fail");
    });

    test("the state machine role can stop and describe the Fargate task", () => {
        // A best-effort cancel of a .sync ECS task calls ecs:StopTask; the role already holds it.
        const roleId = stateMachine.Properties.RoleArn["Fn::GetAtt"][0];
        const actions = Object.values<any>(template.findResources("AWS::IAM::Policy"))
            .filter((p) => (p.Properties.Roles ?? []).some((r: any) => r.Ref === roleId))
            .flatMap((p) => p.Properties.PolicyDocument.Statement)
            .filter((s: any) => s.Effect === "Allow")
            .flatMap((s: any) => (Array.isArray(s.Action) ? s.Action : [s.Action]));
        expect(actions).toEqual(expect.arrayContaining(["ecs:StopTask", "ecs:DescribeTasks"]));
    });

    test("the bundle taskTimeout is the config constant and outlasts the state machine", () => {
        const bundle = JSON.parse(fs.readFileSync(BUNDLE_PIPELINE_JSON, "utf-8"));
        // A string in the bundle, as every shipped pipeline.json spells it.
        expect(bundle.executionConfig.taskTimeout).toBe("19800");
        const taskTimeout = Number(bundle.executionConfig.taskTimeout);
        expect(taskTimeout).toBe(Config.RAPID_PIPELINE_ECS_BUNDLE_TASK_TIMEOUT_SECONDS);
        expect(bundle.executionConfig.waitForCallback).toBe("Enabled");
        // The parent's task starts when it invokes vamsExecute, which runs openPipeline to completion
        // before returning; the execution starts inside that call. The margin must cover it.
        const vamsExecute = Object.values<any>(
            template.findResources("AWS::Lambda::Function")
        ).find((fn) => fn.Properties.Handler === "vamsExecuteRapidPipeline.lambda_handler");
        expect(vamsExecute).toBeDefined();
        expect(taskTimeout - definition.TimeoutSeconds).toBeGreaterThanOrEqual(
            vamsExecute.Properties.Timeout
        );
    });
});
