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

export interface VamsSchemaRegistrationProps {
    /**
     * Name of the V2 import custom-resource lambda (ApiBuilder2.importGlobalPipelineWorkflowV2FunctionName).
     */
    importFunctionName: string;
    /** Artefacts bucket the vamsSchema files are uploaded to (same bucket the import lambda reads). */
    artefactsBucket: s3.IBucket;
    /**
     * Absolute path to the pipeline's `vamsSchema/` directory. Must contain at least `pipeline.json`;
     * `workflow.json` and `templates/*.json` are optional (minimal-required ingestion).
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
}

/**
 * VamsSchemaRegistration
 *
 * Uploads a pipeline's `vamsSchema/` directory to the artefacts bucket and wires a CloudFormation
 * custom resource that registers the built-in pipeline + workflow into the V2 tables at deploy
 * (via the V2 import lambda). Redeploys upsert (unarchive + overwrite); teardown archives.
 *
 * The upload is seamless — the developer only points at the `vamsSchema/` dir; no manual S3 steps.
 */
export class VamsSchemaRegistration extends Construct {
    constructor(scope: Construct, id: string, props: VamsSchemaRegistrationProps) {
        super(scope, id);

        const dir = props.vamsSchemaDir;
        const pipelinePath = path.join(dir, "pipeline.json");
        if (!fs.existsSync(pipelinePath)) {
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

        // Compute the S3 keys present, mirroring the on-disk layout (minimal-required: only
        // pipeline.json is guaranteed; workflow.json + templates/*.json are optional).
        const bundleS3Keys: { pipeline: string; workflow?: string; templates?: string[] } = {
            pipeline: `${prefix}/pipeline.json`,
        };
        if (fs.existsSync(path.join(dir, "workflow.json"))) {
            bundleS3Keys.workflow = `${prefix}/workflow.json`;
        }
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

        // Content hash of the schema files + overrides so CloudFormation re-invokes the CR whenever
        // the schema (or an injected resource/id value) changes — a template/config edit + redeploy
        // then re-registers. Without this, the CR properties are stable across edits and CFN would
        // not re-run the resource.
        const schemaHash = this.hashSchema(
            dir,
            props.resourceOverrides,
            props.idOverrides,
            props.triggerEnabled
        );

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

    /** SHA-256 over every schema file's contents + the injected overrides, so any change re-runs the CR. */
    private hashSchema(
        dir: string,
        resourceOverrides?: { [key: string]: string },
        idOverrides?: { [key: string]: string },
        triggerEnabled?: boolean
    ): string {
        const hash = crypto.createHash("sha256");
        const addFile = (p: string) => {
            if (fs.existsSync(p)) {
                hash.update(p);
                hash.update(fs.readFileSync(p));
            }
        };
        addFile(path.join(dir, "pipeline.json"));
        addFile(path.join(dir, "workflow.json"));
        const templatesDir = path.join(dir, "templates");
        if (fs.existsSync(templatesDir) && fs.statSync(templatesDir).isDirectory()) {
            for (const f of fs.readdirSync(templatesDir).sort()) {
                addFile(path.join(templatesDir, f));
            }
        }
        hash.update(JSON.stringify(resourceOverrides || {}));
        hash.update(JSON.stringify(idOverrides || {}));
        hash.update(JSON.stringify(triggerEnabled ?? null));
        return hash.digest("hex");
    }
}
