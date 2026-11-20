/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as fs from "fs";
import * as path from "path";
import * as crypto from "crypto";
import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deployment from "aws-cdk-lib/aws-s3-deployment";
import * as cr from "aws-cdk-lib/custom-resources";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";

/**
 * Deployment-time overrides of the registered pipeline's executionConfig. Each field replaces the
 * bundle's own value, so a deployment can register an existing bundle with a different execution
 * config without shipping a second bundle. Omit the property (or every field) to register the bundle
 * exactly as it is.
 */
export interface ExecutionConfigOverrides {
    /** Replaces executionConfig.waitForCallback. */
    waitForCallback?: "Enabled" | "Disabled";
    /** Replaces executionConfig.taskTimeout (seconds). */
    taskTimeout?: string | number;
    /** Replaces executionConfig.taskHeartbeatTimeout (seconds; "" clears it). */
    taskHeartbeatTimeout?: string | number;
    /** Replaces the Lambda invoke target (executionConfig.lambda.resourceId; Lambda execution type). */
    lambdaName?: string;
}

export interface VamsSchemaRegistrationProps {
    /**
     * Name of the V2 import custom-resource lambda (ApiBuilder2.importGlobalPipelineWorkflowV2FunctionName).
     */
    importFunctionName: string;
    /** Artefacts bucket the vamsSchema files are uploaded to (same bucket the import lambda reads). */
    artefactsBucket: s3.IBucket;
    /**
     * Absolute path to the pipeline's `vamsSchema/` directory. Must contain `pipeline.json`;
     * `workflow.json` and `templates/*.json` are optional. A bundle without a workflow registers the
     * pipeline alone, for use as a step in an operator-authored workflow.
     */
    vamsSchemaDir: string;
    /**
     * Deploy-time resolved execution resource values injected into the pipeline executionConfig by
     * the import lambda (schema files carry no hard-coded ARNs). Supply the ones relevant to the
     * pipeline's executionType, e.g. { lambdaName } / { sqsQueueUrl } / { eventBridgeBusArn, ... }.
     */
    resourceOverrides?: { [key: string]: string };
    /**
     * Optional id overrides so a built-in keeps a known pipeline/workflow id across deployments
     * (external references). Keys: pipelineId, pipelineDatabaseId, workflowId, workflowDatabaseId.
     */
    idOverrides?: { [key: string]: string };
    /**
     * Deploy-time enable for the bundle's fileUpload trigger (the pipeline's
     * `autoRegisterAutoTriggerOnFileUpload` config). When set, it overrides the trigger's `enabled`
     * flag in the schema so a built-in ships its trigger definition but only auto-fires when the
     * deployment opts in. Omit to leave the schema value intact.
     */
    triggerEnabled?: boolean;
    /**
     * Deploy-time override of the pipeline's executionConfig (waitForCallback, taskTimeout,
     * taskHeartbeatTimeout, Lambda name). Absent leaves the registration request exactly as it was.
     * Changing it re-runs the registration, and the pipeline service then redeploys every workflow
     * that references the pipeline.
     */
    executionConfigOverrides?: ExecutionConfigOverrides;
}

/**
 * VamsSchemaRegistration
 *
 * Uploads a pipeline's `vamsSchema/` directory to the artefacts bucket and wires a CloudFormation
 * custom resource that registers the built-in pipeline, its templates and its workflow into the V2
 * tables at deploy (via the V2 import lambda). Redeploys upsert (unarchive + overwrite); teardown
 * archives.
 *
 * The upload is seamless — the developer only points at the `vamsSchema/` dir; no manual S3 steps.
 */
export class VamsSchemaRegistration extends Construct {
    constructor(scope: Construct, id: string, props: VamsSchemaRegistrationProps) {
        super(scope, id);

        const dir = props.vamsSchemaDir;
        // Synth-time read of the pipeline's own vamsSchema directory (a __dirname-relative constant
        // from the calling construct) and its fixed file names; nothing here comes from request
        // input.
        // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
        if (!fs.existsSync(path.join(dir, "pipeline.json"))) {
            throw new Error(`VamsSchemaRegistration: required pipeline.json not found in ${dir}`);
        }

        // Deterministic per-registration prefix so redeploys overwrite the same keys and distinct
        // registrations do not collide. A short hash of the construct's unique id keeps the prefix
        // well under the BucketDeployment destinationKeyPrefix limit (<=104 chars) — the full
        // uniqueId of a deeply-nested pipeline construct alone can exceed it.
        const registrationHash = crypto
            .createHash("sha256")
            .update(cdk.Names.uniqueId(this))
            .digest("hex")
            .slice(0, 16);
        const prefix = `vamsSchema/${registrationHash}`;

        // Upload the whole vamsSchema dir; the import lambda reads only the keys we compute below.
        const deployment = new s3deployment.BucketDeployment(this, "SchemaDeploy", {
            sources: [s3deployment.Source.asset(dir)],
            destinationBucket: props.artefactsBucket,
            destinationKeyPrefix: prefix,
            prune: false,
        });

        // Compute the S3 keys present, mirroring the on-disk layout (workflow.json and
        // templates/*.json are optional). A key must be emitted only when the file exists: the import
        // lambda treats every key it receives as mandatory and fails the custom resource when the
        // object is missing, so an unconditional workflow key turns an absent workflow.json from a
        // synth error into a mid-deploy one.
        const bundleS3Keys: { pipeline: string; workflow?: string; templates?: string[] } = {
            pipeline: `${prefix}/pipeline.json`,
        };
        // Synth-time read of the pipeline's own vamsSchema directory (a __dirname-relative constant
        // from the calling construct) and its fixed file names; nothing here comes from request
        // input.
        // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
        if (fs.existsSync(path.join(dir, "workflow.json"))) {
            bundleS3Keys.workflow = `${prefix}/workflow.json`;
        }
        // Synth-time read of the pipeline's own vamsSchema directory (a __dirname-relative constant
        // from the calling construct) and its fixed file names; nothing here comes from request
        // input.
        // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
        const templatesDir = path.join(dir, "templates");
        if (fs.existsSync(templatesDir) && fs.statSync(templatesDir).isDirectory()) {
            const templateFiles = fs
                .readdirSync(templatesDir)
                .filter((f) => f.endsWith(".json") && !f.endsWith(".webform.json"))
                .sort();
            if (templateFiles.length > 0) {
                bundleS3Keys.templates = templateFiles.map((f) => `${prefix}/templates/${f}`);
            }
        }

        const importFunction = lambda.Function.fromFunctionName(
            this,
            "ImportFn",
            props.importFunctionName
        );

        const provider = new cr.Provider(this, "Provider", {
            onEventHandler: importFunction,
        });

        // Content hash of the schema files so CloudFormation re-invokes the CR whenever the schema
        // changes — a template edit + redeploy then re-registers. Without this, the CR properties are
        // stable across edits and CFN would not re-run the resource. A retarget of an override value
        // is caught by that property changing, not by this hash.
        const schemaHash = this.hashSchema(
            dir,
            props.resourceOverrides,
            props.idOverrides,
            props.triggerEnabled,
            props.executionConfigOverrides
        );
        const executionConfigOverrides = this.definedOverrides(props.executionConfigOverrides);

        const resource = new cdk.CustomResource(this, "Registration", {
            serviceToken: provider.serviceToken,
            properties: {
                bundleS3Keys: JSON.stringify(bundleS3Keys),
                resourceOverrides: JSON.stringify(props.resourceOverrides || {}),
                idOverrides: JSON.stringify(props.idOverrides || {}),
                // Only emit the trigger-enable override when explicitly set, so a bundle without a
                // trigger (or one relying on its schema default) is unaffected. The value participates
                // in schemaHash so a toggle re-runs the CR.
                ...(props.triggerEnabled !== undefined
                    ? { triggerEnabled: props.triggerEnabled ? "true" : "false" }
                    : {}),
                // Only emitted when at least one field is set, so a registration without overrides
                // keeps exactly the properties (and request) it had before.
                ...(executionConfigOverrides
                    ? { executionConfigOverrides: JSON.stringify(executionConfigOverrides) }
                    : {}),
                schemaHash,
            },
        });

        // Ensure the files are uploaded before the CR runs, and re-run the CR after every upload so
        // schema edits are picked up (BucketDeployment replaces objects in place under the prefix).
        resource.node.addDependency(deployment);

        // The CDK-generated framework roles inside this construct (the BucketDeployment uploader and
        // the custom-resource Provider's onEvent handler) carry wildcard IAM the per-lambda helper
        // cannot reach. Their IAM4 (managed policies) + wildcard S3/lambda-invoke are suppressed
        // centrally by suppressCdkNagLambdaFrameworkResources() (framework-onEvent / CDKBucketDeployment
        // markers), applied once on the core stack.
    }

    /** The override fields that are set, or undefined when none is (so absent == no property). */
    private definedOverrides(
        overrides?: ExecutionConfigOverrides
    ): { [key: string]: string } | undefined {
        const defined = Object.entries(overrides || {})
            .filter(([, v]) => v !== undefined && v !== null)
            .map(([k, v]) => [k, String(v)]);
        return defined.length > 0 ? Object.fromEntries(defined) : undefined;
    }

    /** SHA-256 over every schema file's contents + the injected overrides, so any change re-runs the CR. */
    private hashSchema(
        dir: string,
        resourceOverrides?: { [key: string]: string },
        idOverrides?: { [key: string]: string },
        triggerEnabled?: boolean,
        executionConfigOverrides?: ExecutionConfigOverrides
    ): string {
        const hash = crypto.createHash("sha256");
        // The file's path relative to the bundle dir, so an identical bundle hashes the same from any
        // checkout location or OS (an absolute path would make CI and a developer machine disagree).
        const addFile = (relativePath: string) => {
            // Synth-time read of the pipeline's own vamsSchema directory (a __dirname-relative
            // constant from the calling construct) and its fixed file names; nothing here comes
            // from request input.
            // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
            const p = path.join(dir, relativePath);
            if (fs.existsSync(p) && fs.statSync(p).isFile()) {
                hash.update(relativePath.split(path.sep).join("/"));
                hash.update(fs.readFileSync(p));
            }
        };
        addFile("pipeline.json");
        addFile("workflow.json");
        // Synth-time read of the pipeline's own vamsSchema directory (a __dirname-relative constant
        // from the calling construct) and its fixed file names; nothing here comes from request
        // input.
        // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
        const templatesDir = path.join(dir, "templates");
        if (fs.existsSync(templatesDir) && fs.statSync(templatesDir).isDirectory()) {
            // Same selection the import lambda reads via bundleS3Keys: top-level *.json excluding the
            // *.webform.json UI companions. A nested directory is skipped rather than read as a file.
            for (const f of fs
                .readdirSync(templatesDir)
                .filter((f) => f.endsWith(".json") && !f.endsWith(".webform.json"))
                .sort()) {
                // Synth-time read of the pipeline's own vamsSchema directory (a __dirname-relative
                // constant from the calling construct) and its fixed file names; nothing here comes
                // from request input.
                // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
                addFile(path.join("templates", f));
            }
        }
        // Override values that are CloudFormation tokens resolve only at deploy time, so their
        // synth-time text is a token index that shifts whenever an unrelated construct is added or
        // removed. Hashing that text would change the hash on deploys where no schema changed and
        // re-run every registration, overwriting operator edits to built-in pipelines. Substitute the
        // key name: the resolved value still reaches CloudFormation through the resourceOverrides and
        // idOverrides properties, which detect a genuine retarget on their own.
        const stableOverrides = (overrides?: { [key: string]: string }) =>
            Object.entries(overrides || {})
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([k, v]) => [k, cdk.Token.isUnresolved(v) ? "<deploy-time>" : v]);
        hash.update(JSON.stringify(stableOverrides(resourceOverrides)));
        hash.update(JSON.stringify(stableOverrides(idOverrides)));
        hash.update(JSON.stringify(triggerEnabled ?? null));
        // Mixed in only when set, so a registration without overrides keeps today's hash and no
        // deployed registration re-runs because of this property. A token (a deploy-time Lambda
        // name) is replaced by a stable marker for the same reason as the resource overrides above.
        const overrides = this.definedOverrides(executionConfigOverrides);
        if (overrides) {
            hash.update(
                JSON.stringify(
                    Object.entries(overrides)
                        .sort(([a], [b]) => a.localeCompare(b))
                        .map(([k, v]) => [k, cdk.Token.isUnresolved(v) ? "<deploy-time>" : v])
                )
            );
        }
        return hash.digest("hex");
    }
}
