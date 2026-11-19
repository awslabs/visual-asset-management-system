/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every job a VAMS pipeline starts carries the pipeline job tags (issue #1704): `vams:stackname` (the
 * deployment) and `vams:pipeline` (the pipeline) on AWS Batch jobs and Amazon ECS tasks, and the
 * `vams/pipeline` label on RapidPipeline's Kubernetes Jobs. A later release scopes each pipeline role's
 * terminate and stop permissions to these tags, so a submit without them would be a job that release
 * cannot reach.
 *
 * The "every site" checks read the SOURCE, as launchTemplateUserDataTokens.test.ts does: a synth-based
 * check covers only the pipelines a test app enables, and a pipeline that submits untagged is invisible
 * while it is disabled. The helper checks synthesize.
 */

import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import commercialTemplate from "../../config/config.template.commercial.json";
import {
    configureLambdaBatchJobTagging,
    PIPELINE_JOB_TAGS_ENVIRONMENT_VARIABLE,
    pipelineJobLabels,
    pipelineJobTags,
    tagEcsTaskDefinition,
    TaggedBatchSubmitJob,
    VAMS_PIPELINE_LABEL_KEY,
    VAMS_PIPELINE_TAG_KEY,
    VAMS_PIPELINE_TAG_VALUES,
    VAMS_STACK_NAME_TAG_KEY,
} from "../../lib/nestedStacks/pipelines/constructs/pipelineJobTags";
import { StateTimeoutBatchSubmitJob } from "../../lib/nestedStacks/pipelines/constructs/stateTimeoutBatchSubmitJob";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";
const REPO_ROOT = path.join(__dirname, "..", "..", "..");
const PIPELINES_DIR = path.join(REPO_ROOT, "infra", "lib", "nestedStacks", "pipelines");
const BACKEND_PIPELINES_DIR = path.join(REPO_ROOT, "backendPipelines");

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-core-test";
    config.app.baseStackName = "test";
    return config;
};

/** Every file under `dir` whose name passes `keep`, skipping test folders. */
function sourcesUnder(dir: string, keep: (name: string) => boolean): string[] {
    const found: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            if (["tests", "test", "node_modules", "__pycache__"].includes(entry.name)) continue;
            found.push(...sourcesUnder(full, keep));
        } else if (keep(entry.name)) {
            found.push(full);
        }
    }
    return found;
}

/** Source with comments removed, so a sentence describing a pattern is not taken for the code. */
const codeOf = (file: string): string =>
    fs
        .readFileSync(file, "utf-8")
        // Block comments that open a line only: an ARN string such as `job/*` holds "/*" too, and
        // stripping from there would drop real code up to the next "*/".
        .replace(/^\s*\/\*[\s\S]*?\*\//gm, "")
        .replace(/^\s*\/\/.*$/gm, "")
        .replace(/^\s*#.*$/gm, "");

const relative = (file: string): string => path.relative(REPO_ROOT, file);

describe("every pipeline job submit carries the pipeline job tags", () => {
    const cdkSources = sourcesUnder(PIPELINES_DIR, (name) => name.endsWith(".ts"));
    const pythonSources = sourcesUnder(BACKEND_PIPELINES_DIR, (name) => name.endsWith(".py"));

    test("the trees are being read", () => {
        expect(cdkSources.length).toBeGreaterThan(20);
        expect(pythonSources.length).toBeGreaterThan(20);
    });

    test("no Step Functions state submits an AWS Batch job without tags", () => {
        // TaggedBatchSubmitJob (and StateTimeoutBatchSubmitJob, which extends it) requires jobTags;
        // a plain BatchSubmitJob carries none and grants no batch:TagResource.
        const offenders = cdkSources
            .filter((file) => !file.endsWith(path.join("constructs", "pipelineJobTags.ts")))
            .filter((file) => /new\s+tasks\.BatchSubmitJob\s*\(/.test(codeOf(file)))
            .map(relative);
        expect(offenders).toEqual([]);
    });

    test("every construct with a tagged Batch state suppresses the tagging grant's wildcards", () => {
        // cdk-nag fails synth only for the pipelines a config enables, so the source scan covers the rest.
        const definitions = ["pipelineJobTags.ts", "stateTimeoutBatchSubmitJob.ts"].map((name) =>
            path.join("constructs", name)
        );
        const users = cdkSources
            .filter((file) => !definitions.some((d) => file.endsWith(d)))
            .filter((file) =>
                /new\s+(TaggedBatchSubmitJob|StateTimeoutBatchSubmitJob)\s*\(/.test(codeOf(file))
            );
        expect(users.length).toBeGreaterThan(0);
        const missing = users
            .filter((file) => !codeOf(file).includes("suppressBatchJobTaggingNag("))
            .map(relative);
        expect(missing).toEqual([]);
    });

    test("every ECS RunTask state propagates tags from a tagged task definition", () => {
        const runTaskFiles = cdkSources.filter((file) =>
            /new\s+(tasks\.EcsRunTask|\w*EcsRunTask)\s*\(/.test(codeOf(file))
        );
        expect(runTaskFiles.length).toBeGreaterThan(0);
        for (const file of runTaskFiles) {
            const code = codeOf(file);
            expect([relative(file), code.includes("tagEcsTaskDefinition(")]).toEqual([
                relative(file),
                true,
            ]);
            expect([
                relative(file),
                code.includes("propagatedTagSource: ecs.PropagatedTagSource.TASK_DEFINITION"),
            ]).toEqual([relative(file), true]);
        }
    });

    test("every Lambda that submits an AWS Batch job tags it, and its construct grants the tagging", () => {
        const submitters = pythonSources.filter((file) => /\.submit_job\(/.test(codeOf(file)));
        expect(submitters.length).toBeGreaterThan(0);
        for (const file of submitters) {
            const code = codeOf(file);
            expect([relative(file), code.includes(PIPELINE_JOB_TAGS_ENVIRONMENT_VARIABLE)]).toEqual(
                [relative(file), true]
            );
            expect([relative(file), /submit_params\["tags"\]/.test(code)]).toEqual([
                relative(file),
                true,
            ]);
            // backendPipelines/<category>/<pipeline>/lambda/x.py <-> infra/lib/nestedStacks/pipelines/<category>/<pipeline>/
            const pipelineDir = path
                .relative(BACKEND_PIPELINES_DIR, file)
                .split(path.sep)
                .slice(0, 2);
            const constructDir = path.join(PIPELINES_DIR, ...pipelineDir);
            const grants = sourcesUnder(constructDir, (name) => name.endsWith(".ts")).some((ts) =>
                codeOf(ts).includes("configureLambdaBatchJobTagging(")
            );
            expect([relative(file), grants]).toEqual([relative(file), true]);
        }
    });

    test("every Lambda that creates a Kubernetes Job labels it", () => {
        const creators = pythonSources.filter((file) =>
            /create_namespaced_job\(/.test(codeOf(file))
        );
        expect(creators.length).toBeGreaterThan(0);
        for (const file of creators) {
            // The Job manifest is built in the handler that hands it to create_namespaced_job.
            const pipelineLambdaDir = path.dirname(file);
            const labelled = sourcesUnder(pipelineLambdaDir, (name) => name.endsWith(".py")).some(
                (py) => /\*\*PIPELINE_JOB_LABELS/.test(codeOf(py))
            );
            expect([relative(file), labelled]).toEqual([relative(file), true]);
        }
        const builders = cdkSources.filter((file) => codeOf(file).includes("PIPELINE_JOB_LABELS:"));
        expect(builders.map((file) => codeOf(file).includes("pipelineJobLabels("))).toEqual(
            builders.map(() => true)
        );
    });
});

describe("pipeline job tag values", () => {
    test("the two tags name the deployment and the pipeline", () => {
        expect(pipelineJobTags(createMockConfig(), VAMS_PIPELINE_TAG_VALUES.zipArchive)).toEqual({
            "vams:stackname": "vams-core-test",
            "vams:pipeline": "zipArchive",
        });
        expect(VAMS_STACK_NAME_TAG_KEY).toBe("vams:stackname");
        expect(VAMS_PIPELINE_TAG_KEY).toBe("vams:pipeline");
    });

    test("a deployment with no core stack name is refused rather than tagged with an empty value", () => {
        const config = createMockConfig();
        config.env.coreStackName = "";
        expect(() => pipelineJobTags(config, VAMS_PIPELINE_TAG_VALUES.zipArchive)).toThrow(
            /coreStackName/
        );
    });

    test("every pipeline value is unique and a valid Kubernetes label value", () => {
        const values = Object.values(VAMS_PIPELINE_TAG_VALUES);
        expect(new Set(values).size).toBe(values.length);
        for (const value of values) {
            expect(value).toMatch(/^[A-Za-z0-9]{1,63}$/);
        }
        expect(pipelineJobLabels(VAMS_PIPELINE_TAG_VALUES.rapidPipelineEks)).toEqual({
            [VAMS_PIPELINE_LABEL_KEY]: "rapidPipelineEks",
        });
        expect(VAMS_PIPELINE_LABEL_KEY).toBe("vams/pipeline");
    });
});

describe("tag-on-create grants", () => {
    const tags = { "vams:stackname": "vams-core-test", "vams:pipeline": "preview3dThumbnail" };
    const expectedConditions = {
        StringEquals: {
            "aws:RequestTag/vams:stackname": "vams-core-test",
            "aws:RequestTag/vams:pipeline": "preview3dThumbnail",
        },
        "ForAllValues:StringEquals": { "aws:TagKeys": ["vams:stackname", "vams:pipeline"] },
    };

    const newStack = (): cdk.Stack => {
        Service.SetConfig(createMockConfig());
        return new cdk.Stack(newTestApp(), "PipelineJobTagsTestStack", {
            env: { account: ACCOUNT, region: REGION },
        });
    };

    const tagResourceStatements = (template: Template): any[] =>
        Object.values(template.findResources("AWS::IAM::Policy")).flatMap((policy: any) =>
            policy.Properties.PolicyDocument.Statement.filter((s: any) =>
                [].concat(s.Action).includes("batch:TagResource" as never)
            )
        );

    // AWS Batch authorizes SubmitJob's tagging against the job definition and job queue as well as the
    // new job; a grant on job/* alone was refused at the first live submit (PR #1706 smoke).
    const expectTaggingResources = (statement: any): void => {
        const resources = [].concat(statement.Resource).map((r: any) => JSON.stringify(r));
        for (const type of ["job", "job-definition", "job-queue"]) {
            expect(resources.some((r) => r.includes(`:batch:${REGION}:${ACCOUNT}:${type}/*`))).toBe(
                true
            );
        }
        expect(resources).toHaveLength(3);
    };

    test.each([
        ["TaggedBatchSubmitJob", TaggedBatchSubmitJob],
        ["StateTimeoutBatchSubmitJob", StateTimeoutBatchSubmitJob],
    ])(
        "a %s state sends the tags and its state machine role may apply only those",
        (_name, Task) => {
            const stack = newStack();
            const task = new (Task as any)(stack, "Job", {
                jobTags: tags,
                stateTimeout: cdk.Duration.minutes(30),
                jobName: "job",
                jobDefinitionArn: `arn:aws:batch:${REGION}:${ACCOUNT}:job-definition/job`,
                jobQueueArn: `arn:aws:batch:${REGION}:${ACCOUNT}:job-queue/queue`,
                integrationPattern: sfn.IntegrationPattern.RUN_JOB,
            });
            new sfn.StateMachine(stack, "Machine", {
                definitionBody: sfn.DefinitionBody.fromChainable(task),
            });

            expect(stack.resolve(task.toStateJson()).Parameters.Tags).toEqual(tags);
            const statements = tagResourceStatements(Template.fromStack(stack));
            expect(statements).toHaveLength(1);
            expectTaggingResources(statements[0]);
            expect(statements[0].Condition).toEqual(expectedConditions);
        }
    );

    test("a Lambda that submits a job gets the tags as JSON and the same scoped grant", () => {
        const stack = newStack();
        const fn = new lambda.Function(stack, "Submitter", {
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "index.handler",
            code: lambda.Code.fromInline("def handler(e, c): pass"),
        });
        configureLambdaBatchJobTagging(fn, tags);
        const template = Template.fromStack(stack);

        const env = Object.values(template.findResources("AWS::Lambda::Function"))[0] as any;
        expect(JSON.parse(env.Properties.Environment.Variables.PIPELINE_JOB_TAGS)).toEqual(tags);
        const statements = tagResourceStatements(template);
        expect(statements).toHaveLength(1);
        expectTaggingResources(statements[0]);
        expect(statements[0].Condition).toEqual(expectedConditions);
    });

    test("an ECS task definition carries the tags and nothing else is tagged by them", () => {
        const stack = newStack();
        const taskDefinition = new ecs.FargateTaskDefinition(stack, "TaskDef", {
            cpu: 256,
            memoryLimitMiB: 512,
        });
        taskDefinition.addContainer("C", { image: ecs.ContainerImage.fromRegistry("busybox") });
        tagEcsTaskDefinition(taskDefinition, tags);
        const template = Template.fromStack(stack);

        const definition = Object.values(
            template.findResources("AWS::ECS::TaskDefinition")
        )[0] as any;
        expect(definition.Properties.Tags).toEqual(
            expect.arrayContaining([
                { Key: "vams:stackname", Value: "vams-core-test" },
                { Key: "vams:pipeline", Value: "preview3dThumbnail" },
            ])
        );
        // The task role the definition creates is not tagged with the job tags.
        for (const role of Object.values(template.findResources("AWS::IAM::Role")) as any[]) {
            const keys = (role.Properties.Tags ?? []).map((t: any) => t.Key);
            expect(keys).not.toContain("vams:pipeline");
        }
        // No tagging grant is needed: the tags reach the task by propagation, not in RunTask.
        expect(
            JSON.stringify(template.findResources("AWS::IAM::Policy")).includes("ecs:TagResource")
        ).toBe(false);
    });
});
