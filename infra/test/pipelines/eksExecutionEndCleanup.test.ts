/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A RapidPipeline EKS execution that ends without PipelineEnd still has its Kubernetes job deleted.
 *
 * Stopping the execution (the VAMS execution abort, or a failure elsewhere in the workflow, which
 * stops every registered sub-process), its state machine timeout, or an error no Catch routes, ends
 * the run without PipelineEnd, the only state that deletes the job. Step Functions sends each status
 * change of a standard execution to the default event bus, so an Amazon EventBridge rule hands this
 * state machine's ABORTED, TIMED_OUT and FAILED changes to the consolidated handler's
 * EXECUTION_END_CLEANUP. The cleanup finds the run's jobs from the records CONSTRUCT_PIPELINE writes
 * under the execution's name, and its order of operations is pinned by
 * `test_rapidpipeline_eks_execution_end_cleanup.py`.
 *
 * A cleanup that did not complete fails the invocation, so Lambda's own retries of the handler's
 * asynchronous invocations run it again; they are pinned by an EventInvokeConfig with no destination.
 *
 * The rule's target is the one new resource-based permission: lambda:InvokeFunction for the
 * EventBridge service principal, conditioned on SourceArn = this rule and SourceAccount = the
 * deploying account. Asserted on every shipped partition's template, with the EKS pipeline switched
 * on (no shipped template enables it), so a hardcoded partition or a missing condition shows in the
 * partition it breaks. The positive control in each is that the EKS state machine and the handler it
 * invokes are in that synth.
 */

import * as fs from "fs";
import * as path from "path";
import { Resource, SynthResult, TemplateName, synthTemplate } from "../support/templateSynth";

jest.setTimeout(900_000);

const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];

const PARTITION: Record<TemplateName, string> = {
    commercial: "aws",
    govcloud: "aws-us-gov",
    eusovereign: "aws-eusc",
};

const CLEANUP_OPERATION = "EXECUTION_END_CLEANUP";
const CLEANUP_STATUSES = ["ABORTED", "TIMED_OUT", "FAILED"];
const RUN_JOBS_PREFIX = "pipelines/rapid-pipeline-eks/run-jobs/";

const HANDLER_SOURCE = path.resolve(
    __dirname,
    "../../../backendPipelines/multi/rapidPipelineEKS/lambda/consolidated_handler.py"
);

function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.enabled = true;
    c.app.pipelines.useRapidPipeline.autoRegisterWithVAMS = false;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

const synthWithEks = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enableEksPipeline, mutateKey: `eks-execution-end-${name}` });

interface EksStateMachine {
    stack: string;
    logicalId: string;
    resource: any;
    definition: any;
}

/** The EKS state machine: the one whose flattened definition carries RunJob and InitializeCounter. */
function eksStateMachine(synth: SynthResult): EksStateMachine {
    const found = synth.ofType("AWS::StepFunctions::StateMachine").flatMap((r) => {
        const flattened = SynthResult.flatten(r.properties.DefinitionString);
        return flattened.includes('"RunJob"') && flattened.includes('"InitializeCounter"')
            ? [
                  {
                      stack: r.stack,
                      logicalId: r.logicalId,
                      resource: r.raw,
                      definition: JSON.parse(flattened),
                  },
              ]
            : [];
    });
    expect(found).toHaveLength(1);
    return found[0];
}

/** The function a Lambda Task state invokes, from its `${LogicalId}` / `${LogicalId.Arn}` reference. */
function invokedFunctionId(state: any): string {
    const match = /^\$\{([A-Za-z0-9]+)(?:\.Arn)?\}$/.exec(String(state.Parameters?.FunctionName));
    if (!match) throw new Error(`cannot resolve the function ${state.Parameters?.FunctionName}`);
    return match[1];
}

const isGetAttArn = (value: any, logicalId: string): boolean =>
    JSON.stringify(value) === JSON.stringify({ "Fn::GetAtt": [logicalId, "Arn"] });

/** Every `arn:<partition>:` prefix in a flattened value. */
const arnPartitions = (value: any): string[] =>
    [...SynthResult.flatten(value).matchAll(/arn:([^:]*):/g)].map((m) => m[1]);

describe.each(TEMPLATES)("%s: EKS execution-end cleanup", (name) => {
    let synth: SynthResult;
    let sm: EksStateMachine;
    let handlerId: string;
    let handler: any;

    beforeAll(() => {
        synth = synthWithEks(name);
        sm = eksStateMachine(synth);
        handlerId = invokedFunctionId(sm.definition.States.RunJob);
        handler = synth.templates[sm.stack].Resources[handlerId];
    });

    /** The rules in the state machine's stack that target the handler. */
    const handlerRules = (): Resource[] =>
        synth
            .ofType("AWS::Events::Rule")
            .filter(
                (r) =>
                    r.stack === sm.stack &&
                    (r.properties.Targets ?? []).some((t: any) => isGetAttArn(t.Arn, handlerId))
            );

    /** The resource-based permissions on the handler. */
    const handlerPermissions = (): Resource[] =>
        synth
            .ofType("AWS::Lambda::Permission")
            .filter(
                (p) => p.stack === sm.stack && isGetAttArn(p.properties.FunctionName, handlerId)
            );

    test("[control] the EKS pipeline, its state machine and the handler it invokes are in this synth", () => {
        expect(synth.partition).toBe(PARTITION[name]);
        expect(synth.countOfType("Custom::AWSCDK-EKS-Cluster")).toBe(1);
        expect(sm.definition.States.RunJob.Parameters.Payload.operation).toBe("RUN_JOB");
        expect(handler?.Type).toBe("AWS::Lambda::Function");
    });

    test("one rule targets the handler, and it is the only rule on Step Functions events", () => {
        expect(handlerRules()).toHaveLength(1);
        const onStepFunctions = synth
            .ofType("AWS::Events::Rule")
            .filter((r) => (r.properties.EventPattern?.source ?? []).includes("aws.states"));
        expect(onStepFunctions.map((r) => r.logicalId)).toEqual([handlerRules()[0].logicalId]);
    });

    test("the rule matches only this state machine's ABORTED, TIMED_OUT and FAILED changes", () => {
        const rule = handlerRules()[0];
        expect(rule.properties.EventPattern).toEqual({
            source: ["aws.states"],
            "detail-type": ["Step Functions Execution Status Change"],
            detail: {
                stateMachineArn: [{ Ref: sm.logicalId }],
                status: CLEANUP_STATUSES,
            },
        });
        // The default bus, where Step Functions sends these events.
        expect(rule.properties.EventBusName).toBeUndefined();
        expect(rule.properties.State).toBe("ENABLED");
    });

    test("the target sends the cleanup operation with the execution it names, retried twice within an hour", () => {
        const targets = handlerRules()[0].properties.Targets;
        expect(targets).toHaveLength(1);
        const target = targets[0];
        expect(target.RetryPolicy).toEqual({
            MaximumRetryAttempts: 2,
            MaximumEventAgeInSeconds: 3600,
        });
        expect(target.DeadLetterConfig).toBeUndefined();
        expect(target.RoleArn).toBeUndefined();

        const transformer = target.InputTransformer;
        expect(transformer).toBeDefined();
        const template = SynthResult.flatten(transformer.InputTemplate).replace(
            /<([A-Za-z0-9_-]+)>/g,
            (_, key) => JSON.stringify(`path:${transformer.InputPathsMap[key]}`)
        );
        expect(JSON.parse(template)).toEqual({
            operation: CLEANUP_OPERATION,
            executionArn: "path:$.detail.executionArn",
            executionName: "path:$.detail.name",
            stateMachineArn: "path:$.detail.stateMachineArn",
            status: "path:$.detail.status",
        });
    });

    test("the handler's one resource-based permission is scoped to the rule and the account", () => {
        const permissions = handlerPermissions();
        expect(permissions).toHaveLength(1);
        const permission = permissions[0].properties;
        expect(permission.Action).toBe("lambda:InvokeFunction");
        expect(permission.Principal).toBe("events.amazonaws.com");
        expect(permission.SourceArn).toEqual({
            "Fn::GetAtt": [handlerRules()[0].logicalId, "Arn"],
        });
        expect(permission.SourceAccount).toEqual({ Ref: "AWS::AccountId" });
    });

    test("Lambda retries the handler's asynchronous invocations twice within an hour, with no destination", () => {
        // The handler fails an EXECUTION_END_CLEANUP that did not complete, so these retries run the
        // cleanup again. Pinned rather than left at Lambda's defaults (2 retries, 6 hours), and with
        // no destination, which would add a permission to the handler's role.
        const configs = synth
            .ofType("AWS::Lambda::EventInvokeConfig")
            .filter((c) => c.stack === sm.stack);
        expect(configs).toHaveLength(1);
        expect(configs[0].properties).toEqual({
            FunctionName: { Ref: handlerId },
            Qualifier: "$LATEST",
            MaximumRetryAttempts: 2,
            MaximumEventAgeInSeconds: 3600,
        });
        expect(configs[0].properties.DestinationConfig).toBeUndefined();
    });

    test("every Step Functions task invokes the handler synchronously, so the rule is its only asynchronous caller", () => {
        const handlerTasks = Object.entries<any>(sm.definition.States).filter(
            ([, state]) =>
                state.Type === "Task" &&
                String(state.Resource).includes(":lambda:invoke") &&
                invokedFunctionId(state) === handlerId
        );
        expect(handlerTasks.map(([stateName]) => stateName).sort()).toEqual([
            "CheckJob",
            "ConstructPipeline",
            "PipelineEnd",
            "PipelineEndSuccess",
            "RunJob",
        ]);
        for (const [, state] of handlerTasks) {
            // lambda:invoke without .waitForTaskToken, and Invoke's default invocation type.
            expect(state.Resource).toMatch(/:states:::lambda:invoke$/);
            expect(state.Parameters.InvocationType ?? "RequestResponse").toBe("RequestResponse");
        }
    });

    test("the handler names this state machine's ARN in this partition", () => {
        const value = SynthResult.flatten(
            handler.Properties.Environment?.Variables?.STATE_MACHINE_ARN
        );
        const smName = sm.resource.Properties.StateMachineName;
        expect(typeof smName).toBe("string");
        expect(value).toMatch(
            new RegExp(
                `^arn:${PARTITION[name]}:states:(${synth.region}|\\$\\{AWS::Region\\}):` +
                    `(123456789012|\\$\\{AWS::AccountId\\}):stateMachine:${smName}$`
            )
        );
    });

    test("no ARN in the rule, the permission or the handler's variables names another partition", () => {
        const values = [
            handlerRules()[0].properties,
            ...handlerPermissions().map((p) => p.properties),
            handler.Properties.Environment?.Variables,
        ];
        const partitions = values.flatMap(arnPartitions);
        expect(partitions.length).toBeGreaterThan(0); // control: STATE_MACHINE_ARN is one
        for (const partition of partitions) {
            expect(["${AWS::Partition}", PARTITION[name]]).toContain(partition);
        }
    });
});

describe("commercial: what the cleanup reads", () => {
    let synth: SynthResult;
    let sm: EksStateMachine;

    beforeAll(() => {
        synth = synthWithEks("commercial");
        sm = eksStateMachine(synth);
    });

    /** The one auxiliary bucket, which the storage nested stack creates. */
    const auxiliaryRules = (): any[] => {
        const buckets = synth
            .ofType("AWS::S3::Bucket")
            .filter(
                (b) =>
                    /StorageResourcesBuilder/.test(b.stack) &&
                    b.logicalId.startsWith("AssetAuxiliaryBucket")
            );
        expect(buckets).toHaveLength(1);
        return buckets[0].properties.LifecycleConfiguration?.Rules ?? [];
    };

    test("ConstructPipeline passes the execution name, from the context object", () => {
        // A context-object path is always present, so it cannot fail the task with States.Runtime.
        const payload = sm.definition.States.ConstructPipeline.Parameters.Payload;
        expect(payload.operation).toBe("CONSTRUCT_PIPELINE");
        expect(payload["executionName.$"]).toBe("$$.Execution.Name");
    });

    test("the auxiliary bucket expires the run-jobs records, their noncurrent versions and delete markers", () => {
        const rules = auxiliaryRules().filter((rule) => rule.Prefix === RUN_JOBS_PREFIX);
        expect(rules).toHaveLength(2);
        const expiry = rules.filter((rule) => rule.ExpirationInDays !== undefined);
        expect(expiry).toHaveLength(1);
        expect(expiry[0].ExpirationInDays).toBe(1);
        expect(expiry[0].NoncurrentVersionExpiration).toEqual({ NoncurrentDays: 1 });
        expect(expiry[0].Status).toBe("Enabled");
        expect(expiry[0].ExpiredObjectDeleteMarker).toBeUndefined();
        const markers = rules.filter((rule) => rule.ExpiredObjectDeleteMarker === true);
        expect(markers).toHaveLength(1);
        expect(markers[0].ExpirationInDays).toBeUndefined();
    });

    test("a record outlives the longest execution plus the event's maximum ages", () => {
        // CONSTRUCT_PIPELINE writes the record when the run starts; the cleanup lists it at most the
        // state machine timeout, the rule target's maximum event age (EventBridge's delivery) and the
        // handler's maximum event age (Lambda's asynchronous retries) later.
        const expiry = auxiliaryRules().find(
            (rule) => rule.Prefix === RUN_JOBS_PREFIX && rule.ExpirationInDays !== undefined
        );
        const timeoutSeconds = sm.definition.TimeoutSeconds;
        expect(typeof timeoutSeconds).toBe("number");
        const rule = synth
            .ofType("AWS::Events::Rule")
            .find((r) => (r.properties.EventPattern?.source ?? []).includes("aws.states"));
        const deliveryAge = rule?.properties.Targets[0].RetryPolicy.MaximumEventAgeInSeconds;
        expect(typeof deliveryAge).toBe("number");
        const invokeConfigs = synth
            .ofType("AWS::Lambda::EventInvokeConfig")
            .filter((c) => c.stack === sm.stack);
        expect(invokeConfigs).toHaveLength(1);
        const asyncAge = invokeConfigs[0].properties.MaximumEventAgeInSeconds;
        expect(typeof asyncAge).toBe("number");
        expect(expiry.ExpirationInDays * 86400).toBeGreaterThan(
            timeoutSeconds + deliveryAge + asyncAge
        );
    });

    test("the handler reads what the construct sets, and handles the operation the target sends", () => {
        const source = fs.readFileSync(HANDLER_SOURCE, "utf-8");
        expect(source).toMatch(
            /^RUN_JOBS_RECORD_PREFIX = "pipelines\/rapid-pipeline-eks\/run-jobs\/"$/m
        );
        expect(source).toContain("os.environ.get('STATE_MACHINE_ARN', '')");
        expect(source).toMatch(/supported_operations = \[[^\]]*'EXECUTION_END_CLEANUP'[^\]]*\]/);
        expect(source).toContain(
            "EXECUTION_END_STATUSES = frozenset({'ABORTED', 'TIMED_OUT', 'FAILED'})"
        );
    });
});
