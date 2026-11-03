/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The RapidPipeline EKS run-ended marker: PIPELINE_END writes it before it deletes the run's Kubernetes
 * job, and RUN_JOB deletes a job it creates once the marker exists.
 *
 * RunJob's retrier resends the RUN_JOB invocation after Step Functions-side client errors
 * (`Lambda.ClientExecutionTimeoutException`, `Lambda.SdkClientException`, `Lambda.ServiceException`,
 * `Lambda.AWSLambdaException`), which can be raised while the earlier invocation is still running, so
 * that invocation can create the job after PipelineEnd or PipelineEndSuccess has deleted it. The order
 * of operations that closes this is in the Lambda and is pinned by
 * `test_rapidpipeline_eks_run_ended_marker.py`. What the deployment has to supply is pinned here, on the
 * emitted templates:
 *
 *   * The bucket. The function every RUN_JOB and PIPELINE_END state invokes names the auxiliary bucket
 *     in `RUN_ENDED_MARKER_BUCKET`, and its existing grant covers the calls the marker needs.
 *   * The cleanup. The auxiliary bucket expires the markers under the handler's fixed prefix, with their
 *     noncurrent versions and delete markers, when the EKS pipeline is deployed and not otherwise.
 *   * The job name. HandleRunJobError, which RUN_JOB's error body leads to, hands PipelineEnd the
 *     manifest's job name: a job can exist then, because an earlier attempt may still create it and a
 *     create the API server applied can still fail on the client. PipelineEnd writes the marker for the
 *     name it is given and deletes that job.
 *   * The route. PipelineEnd goes straight to EndChoice and the state machine waits only in its poll
 *     loop, so nothing the run needs happens after the parent workflow's callback, once VAMS may stop
 *     the execution.
 */

import * as fs from "fs";
import * as path from "path";
import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

/** Where the handler writes the markers, and the variable naming their bucket. */
const MARKER_PREFIX = "pipelines/rapid-pipeline-eks/run-ended/";
const MARKER_BUCKET_ENV = "RUN_ENDED_MARKER_BUCKET";

/** Where the job's name sits in the manifest CONSTRUCT_PIPELINE returns and RUN_JOB submits. */
const MANIFEST_JOB_NAME_PATH = "$.ConstructPipelineResult.Payload.jobManifest.metadata.name";

/** The literal that means "no Kubernetes job exists to clean up". */
const NO_JOB_PLACEHOLDER = "failure-before-creation";

const HANDLER_SOURCE = path.resolve(
    __dirname,
    "../../../backendPipelines/multi/rapidPipelineEKS/lambda/consolidated_handler.py"
);

function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

/**
 * The EKS state machine's states and the template that emits it. The definition is an `Fn::Join` of
 * literal fragments around CloudFormation tokens, flattened so each token becomes `${LogicalId...}`.
 */
function eksStateMachine(synth: SynthResult): { stack: string; states: Record<string, any> } {
    for (const [stack, template] of Object.entries<any>(synth.templates)) {
        for (const resource of Object.values<any>(template.Resources ?? {})) {
            if (resource.Type !== "AWS::StepFunctions::StateMachine") continue;
            const definition = SynthResult.flatten(resource.Properties?.DefinitionString);
            if (definition.includes('"RunJob"') && definition.includes('"InitializeCounter"')) {
                return { stack, states: JSON.parse(definition).States };
            }
        }
    }
    throw new Error("no emitted state machine carries the EKS RunJob state");
}

/**
 * The function a Lambda Task state invokes, from `Parameters.FunctionName` of the lambda:invoke
 * integration, which the flattened definition renders as `${LogicalId}` or `${LogicalId.Arn}`.
 */
function invokedFunction(
    synth: SynthResult,
    stack: string,
    state: any
): { logicalId: string; resource: any } {
    const reference: string = String(state.Resource).endsWith(":states:::lambda:invoke")
        ? state.Parameters?.FunctionName
        : state.Resource;
    const match = /^\$\{([A-Za-z0-9]+)(?:\.Arn)?\}$/.exec(String(reference));
    if (!match) throw new Error(`cannot resolve the invoked function from ${reference}`);
    const logicalId = match[1];
    const resource = synth.templates[stack].Resources?.[logicalId];
    if (!resource) throw new Error(`${logicalId} is not a resource in ${stack}`);
    return { logicalId, resource };
}

/** The one auxiliary bucket, which the storage nested stack creates. */
function auxiliaryBucket(synth: SynthResult): Resource {
    const buckets = synth
        .ofType("AWS::S3::Bucket")
        .filter(
            (b) =>
                /StorageResourcesBuilder/.test(b.stack) &&
                b.logicalId.startsWith("AssetAuxiliaryBucket")
        );
    expect(buckets).toHaveLength(1);
    return buckets[0];
}

const lifecycleRules = (bucket: Resource): any[] =>
    bucket.properties.LifecycleConfiguration?.Rules ?? [];

/**
 * The storage-stack reference a nested-stack parameter carries, as `LogicalId` for a Ref or
 * `LogicalId.Attribute` for a GetAtt, or undefined.
 *
 * A storage resource reaches a pipeline nested stack as a parameter named `referenceto…Outputs<output
 * name>`, which every parent stack passes down unchanged, and the storage template's output of that
 * name holds the reference.
 */
function storageReference(synth: SynthResult, stack: string, param: string): string | undefined {
    if (!(param in (synth.templates[stack].Parameters ?? {}))) return undefined;
    const outputName = param.split("Outputs").pop() as string;
    for (const [key, template] of Object.entries<any>(synth.templates)) {
        if (!/StorageResourcesBuilder/.test(key)) continue;
        const value = template.Outputs?.[outputName]?.Value;
        if (typeof value?.Ref === "string") return value.Ref;
        if (Array.isArray(value?.["Fn::GetAtt"])) return value["Fn::GetAtt"].join(".");
    }
    return undefined;
}

/** A template value flattened, with each storage-stack parameter replaced by `<its reference>`. */
const resolved = (synth: SynthResult, stack: string, value: any): string =>
    SynthResult.flatten(value).replace(/\$\{([A-Za-z0-9.]+)\}/g, (token, name) => {
        const target = storageReference(synth, stack, name);
        return target ? `<${target}>` : token;
    });

/** The statements of every policy attached to a function's execution role. */
function roleStatements(synth: SynthResult, stack: string, fn: any): any[] {
    const roleId = fn.Properties?.Role?.["Fn::GetAtt"]?.[0];
    expect(typeof roleId).toBe("string");
    return Object.values<any>(synth.templates[stack].Resources ?? {})
        .filter(
            (r) =>
                r.Type === "AWS::IAM::Policy" &&
                (r.Properties?.Roles ?? []).some((role: any) => role?.Ref === roleId)
        )
        .flatMap((p) => ([] as any[]).concat(p.Properties.PolicyDocument.Statement));
}

/** Whether an IAM action pattern (with `*` wildcards) covers an action. */
const covers = (pattern: string, action: string): boolean =>
    new RegExp(
        "^" +
            pattern
                .split("*")
                .map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&"))
                .join(".*") +
            "$"
    ).test(action);

/** The states that transition into `target`, by Next, Default, a Choice rule or a Catch. */
function predecessors(states: Record<string, any>, target: string): string[] {
    return Object.entries(states)
        .filter(
            ([, s]) =>
                s.Next === target ||
                s.Default === target ||
                (s.Choices ?? []).some((rule: any) => rule.Next === target) ||
                (s.Catch ?? []).some((catcher: any) => catcher.Next === target)
        )
        .map(([name]) => name);
}

describe("EKS run-ended marker", () => {
    let synth: SynthResult;
    let stack: string;
    let states: Record<string, any>;
    let fn: { logicalId: string; resource: any };
    let bucket: Resource;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-run-ended-marker",
        });
        ({ stack, states } = eksStateMachine(synth));
        fn = invokedFunction(synth, stack, states.RunJob);
        bucket = auxiliaryBucket(synth);
    });

    test("the state machine, its RUN_JOB and PIPELINE_END states and the auxiliary bucket are in this synth", () => {
        // The control: the EKS pipeline ships disabled, and every assertion below reads one of these.
        expect(synth.resources.filter((r) => r.type === "Custom::AWSCDK-EKS-Cluster").length).toBe(
            1
        );
        expect(states.RunJob.Parameters.Payload.operation).toBe("RUN_JOB");
        expect(fn.resource.Type).toBe("AWS::Lambda::Function");
        for (const name of ["PipelineEnd", "PipelineEndSuccess"]) {
            expect(states[name].Parameters.Payload.operation).toBe("PIPELINE_END");
            expect(invokedFunction(synth, stack, states[name]).logicalId).toBe(fn.logicalId);
        }
        // Versioned, so a marker that expires leaves a noncurrent version and a delete marker behind.
        expect(bucket.properties.VersioningConfiguration?.Status).toBe("Enabled");
    });

    test("the function those states invoke names the auxiliary bucket in RUN_ENDED_MARKER_BUCKET", () => {
        const value = fn.resource.Properties.Environment?.Variables?.[MARKER_BUCKET_ENV];
        expect(value).toBeDefined();
        expect(resolved(synth, stack, value)).toBe(`<${bucket.logicalId}>`);
    });

    test("the function's existing grant covers writing a marker and reading one back", () => {
        // PutObject writes it; HeadObject needs s3:GetObject, and s3:ListBucket makes a missing key
        // answer 404 rather than 403, which the handler reads as "no marker". No grant is added for the
        // marker: these come from the auxiliary bucket's grantReadWrite on the handler.
        const statements = roleStatements(synth, stack, fn.resource).filter(
            (s) => s.Effect === "Allow"
        );
        const grants = (action: string, resource: string): boolean =>
            statements.some(
                (s) =>
                    ([] as string[]).concat(s.Action).some((a) => covers(a, action)) &&
                    ([] as any[])
                        .concat(s.Resource)
                        .some((r) => resolved(synth, stack, r) === resource)
            );
        const objects = `<${bucket.logicalId}.Arn>/*`;
        expect(grants("s3:PutObject", objects)).toBe(true);
        expect(grants("s3:GetObject", objects)).toBe(true);
        expect(grants("s3:ListBucket", `<${bucket.logicalId}.Arn>`)).toBe(true);
    });

    test("the auxiliary bucket expires the markers, their noncurrent versions and delete markers", () => {
        const rules = lifecycleRules(bucket).filter((rule) => rule.Prefix === MARKER_PREFIX);
        expect(rules).toHaveLength(2);

        const expiry = rules.filter((rule) => rule.ExpirationInDays !== undefined);
        expect(expiry).toHaveLength(1);
        // A marker only has to outlive a RUN_JOB invocation still running when it is written, which
        // the function's 15-minute timeout bounds.
        expect(expiry[0].ExpirationInDays).toBe(1);
        expect(expiry[0].NoncurrentVersionExpiration).toEqual({ NoncurrentDays: 1 });
        expect(expiry[0].Status).toBe("Enabled");
        // S3 rejects ExpiredObjectDeleteMarker in a rule that also carries an Expiration in days.
        expect(expiry[0].ExpiredObjectDeleteMarker).toBeUndefined();

        const markers = rules.filter((rule) => rule.ExpiredObjectDeleteMarker === true);
        expect(markers).toHaveLength(1);
        expect(markers[0].Status).toBe("Enabled");
        expect(markers[0].ExpirationInDays).toBeUndefined();
        expect(markers[0].NoncurrentVersionExpiration).toBeUndefined();
    });

    test("the rules' prefix is the one the handler keys the markers under", () => {
        const source = fs.readFileSync(HANDLER_SOURCE, "utf-8");
        const prefix = /^RUN_ENDED_MARKER_PREFIX = "([^"]+)"$/m.exec(source);
        expect(prefix?.[1]).toBe(MARKER_PREFIX);
        expect(source).toContain('return bucket, f"{RUN_ENDED_MARKER_PREFIX}{k8s_job_name}"');
        expect(source).toContain(`os.environ.get('${MARKER_BUCKET_ENV}', '')`);
    });

    test("HandleRunJobError hands PipelineEnd the name of the job RUN_JOB submitted", () => {
        const handler = states.HandleRunJobError;
        expect(handler.Type).toBe("Pass");
        expect(handler.Parameters["k8sJobName.$"]).toBe(MANIFEST_JOB_NAME_PATH);
        expect(handler.Parameters.k8sJobName).toBeUndefined();
        expect(handler.Next).toBe("PipelineEnd");
    });

    test("that name is in HandleRunJobError's input whenever it runs", () => {
        // A Pass state that reads a missing path fails with States.Runtime, which no Catch routes, so
        // the run would never reach PipelineEnd. HandleRunJobError is entered only from the Choice
        // after RunJob, which RunJob reaches only by returning: its payload read the manifest, and its
        // result lands beside it at $.RunJobResult.
        expect(predecessors(states, "HandleRunJobError")).toEqual(["RunJobSucceeded?"]);
        expect(states["RunJobSucceeded?"].Type).toBe("Choice");
        expect(predecessors(states, "RunJobSucceeded?")).toEqual(["RunJob"]);
        const runJob = states.RunJob;
        expect(runJob.Next).toBe("RunJobSucceeded?");
        expect(runJob.Parameters.Payload["jobManifest.$"]).toBe(
            "$.ConstructPipelineResult.Payload.jobManifest"
        );
        expect(runJob.ResultPath).toBe("$.RunJobResult");
        expect([undefined, "$"]).toContain(runJob.InputPath);
        expect([undefined, "$"]).toContain(runJob.OutputPath);
        // CONSTRUCT_PIPELINE builds every manifest it returns with metadata.name set.
        expect(fs.readFileSync(HANDLER_SOURCE, "utf-8")).toMatch(
            /"metadata": \{\s*"name": f"rapid-pipeline-job-\{job_id\}"/
        );
    });

    test("only ConstructPipeline's failure exits still say that no job exists", () => {
        const withPlaceholder = Object.keys(states)
            .filter((name) => states[name].Parameters?.k8sJobName === NO_JOB_PLACEHOLDER)
            .sort();
        expect(withPlaceholder).toEqual([
            "HandleConstructPipelineError",
            "HandleConstructPipelineInvokeError",
            "HandleConstructPipelineTimeout",
        ]);
    });

    test("PipelineEnd and PipelineEndSuccess go straight to EndChoice", () => {
        expect(states.PipelineEnd.Next).toBe("EndChoice");
        expect(states.PipelineEndSuccess.Next).toBe("EndChoice");
        expect(states.EndChoice.Type).toBe("Choice");
        const ends = [
            ...states.EndChoice.Choices.map((rule: any) => rule.Next),
            states.EndChoice.Default,
        ];
        for (const end of ends) expect(["Fail", "Succeed"]).toContain(states[end].Type);
    });

    test("PipelineEnd and PipelineEndSuccess are the only PIPELINE_END states", () => {
        const ends = Object.keys(states)
            .filter((name) => states[name].Parameters?.Payload?.operation === "PIPELINE_END")
            .sort();
        expect(ends).toEqual(["PipelineEnd", "PipelineEndSuccess"]);
    });

    test("the state machine waits only in its poll loop", () => {
        const waits = Object.keys(states).filter((name) => states[name].Type === "Wait");
        expect(waits.length).toBeGreaterThan(0); // control: the poll loop's Wait is found
        for (const name of waits)
            expect({ name, next: states[name].Next }).toEqual({
                name,
                next: "CheckJob",
            });
    });

    describe("with the EKS pipeline disabled", () => {
        let disabled: SynthResult;

        beforeAll(() => {
            disabled = synthTemplate("commercial");
        });

        test("[control] the auxiliary bucket's lifecycle was read and the pipeline is absent", () => {
            const abort = lifecycleRules(auxiliaryBucket(disabled)).filter(
                (rule) => rule.AbortIncompleteMultipartUpload?.DaysAfterInitiation === 14
            );
            expect(abort).toHaveLength(1);
            expect(disabled.countOfType("Custom::AWSCDK-EKS-Cluster")).toBe(0);
        });

        test("no rule names the marker prefix", () => {
            const rules = lifecycleRules(auxiliaryBucket(disabled)).filter((rule) =>
                String(rule.Prefix ?? "").startsWith("pipelines/rapid-pipeline-eks/")
            );
            expect(rules).toEqual([]);
        });
    });
});
