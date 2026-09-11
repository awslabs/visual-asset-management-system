/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as Config from "../../config/config";

/**
 * Bootstrap ("CDK core") role customization.
 *
 * Replaces the roles created by `cdk bootstrap` (deploy, CloudFormation execution,
 * lookup, file-publishing, image-publishing) with pre-created roles, or removes the
 * bootstrap-role requirement entirely by deploying under the caller's own credentials.
 * Read from the `bootstrap` section of infra/config/policy/iamRoleConfig.json.
 */
export interface BootstrapRoleConfig {
    /**
     * When true, use the CliCredentialsStackSynthesizer (no bootstrap IAM roles at all;
     * deployments run under the caller's credentials). Only the staging bucket/ECR repo
     * are required. Does not support cross-account deploys or CDK Pipelines.
     */
    useCliCredentialsSynthesizer?: boolean;
    /** Bootstrap qualifier (default CDK qualifier is hnb659fds). */
    qualifier?: string;
    /** ARN of the role assumed by the CLI to start the deployment. */
    deployRoleArn?: string;
    /** ARN of the role passed to CloudFormation to execute the deployment. */
    cloudFormationExecutionRoleArn?: string;
    /** ARN of the role used for environment context lookups. */
    lookupRoleArn?: string;
    /** ARN of the role used to publish file assets to the staging bucket. */
    fileAssetPublishingRoleArn?: string;
    /** ARN of the role used to publish Docker image assets to the staging ECR repo. */
    imageAssetPublishingRoleArn?: string;
    /** Name of the staging S3 bucket (only set if the bootstrap template renamed it). */
    fileAssetsBucketName?: string;
    /** Name of the staging ECR repository (only set if the bootstrap template renamed it). */
    imageAssetsRepositoryName?: string;
}

/**
 * VAMS stack role customization.
 *
 * Controls iam.Role.customizeRoles for the VAMS WAF, core, and all nested stacks.
 * Read from the `vamsStacks` section of infra/config/policy/iamRoleConfig.json.
 */
export interface VamsStackRoleConfig {
    /**
     * When true, synthesis still creates the roles but also writes the iam-policy-report
     * to cdk.out (discovery mode). When false, role synthesis is prevented and any role
     * not present in `precreatedRoles` causes synthesis to fail (with the report written).
     */
    generateReportOnly?: boolean;
    /**
     * Map of construct path -> pre-created IAM role name. Construct paths are listed in
     * the iam-policy-report (for example, "vams-core-prod-us-east-1/StorageResourcesBuilder/.../ServiceRole").
     */
    precreatedRoles?: { [constructPath: string]: string };
}

export interface IamRoleCustomizationConfig {
    bootstrap?: BootstrapRoleConfig;
    vamsStacks?: VamsStackRoleConfig;
}

/** Treat empty strings, "UNDEFINED", and null as unset so partial overrides keep CDK defaults. */
function valueOrUndefined(value?: string): string | undefined {
    if (!value || value === "UNDEFINED" || value.trim() === "") {
        return undefined;
    }
    return value.trim();
}

/**
 * Build the stack synthesizer for custom bootstrap roles, or undefined to use the CDK default.
 *
 * Returns a fresh synthesizer instance on each call — a synthesizer binds to a single stack,
 * so the WAF stack and the core stack each need their own instance.
 */
export function buildBootstrapSynthesizer(
    config: Config.Config
): cdk.IStackSynthesizer | undefined {
    if (!config.app.iamRoleConfig || !config.app.iamRoleConfig.useCustomBootstrapRoles) {
        return undefined;
    }

    const bootstrap: BootstrapRoleConfig =
        (config.iamRoleCustomizationJSON && config.iamRoleCustomizationJSON.bootstrap) || {};

    // No bootstrap roles at all — deploy under the caller's own credentials.
    if (bootstrap.useCliCredentialsSynthesizer) {
        return new cdk.CliCredentialsStackSynthesizer({
            qualifier: valueOrUndefined(bootstrap.qualifier),
            fileAssetsBucketName: valueOrUndefined(bootstrap.fileAssetsBucketName),
            imageAssetsRepositoryName: valueOrUndefined(bootstrap.imageAssetsRepositoryName),
        });
    }

    // Pre-created bootstrap roles — only override the fields that are provided.
    return new cdk.DefaultStackSynthesizer({
        qualifier: valueOrUndefined(bootstrap.qualifier),
        deployRoleArn: valueOrUndefined(bootstrap.deployRoleArn),
        cloudFormationExecutionRole: valueOrUndefined(bootstrap.cloudFormationExecutionRoleArn),
        lookupRoleArn: valueOrUndefined(bootstrap.lookupRoleArn),
        fileAssetPublishingRoleArn: valueOrUndefined(bootstrap.fileAssetPublishingRoleArn),
        imageAssetPublishingRoleArn: valueOrUndefined(bootstrap.imageAssetPublishingRoleArn),
        fileAssetsBucketName: valueOrUndefined(bootstrap.fileAssetsBucketName),
        imageAssetsRepositoryName: valueOrUndefined(bootstrap.imageAssetsRepositoryName),
    });
}

/**
 * Apply VAMS stack role customization to the whole app.
 *
 * Calling iam.Role.customizeRoles on the App cascades to every stack and nested stack
 * (the WAF stack, the core stack, and all nested stacks), producing a single
 * iam-policy-report listing every role VAMS would create with full construct paths.
 * Must be called before the stacks are constructed.
 */
export function applyVamsStackRoleCustomization(app: cdk.App, config: Config.Config): void {
    if (!config.app.iamRoleConfig || !config.app.iamRoleConfig.useCustomVamsStackRoles) {
        return;
    }

    const vamsStacks: VamsStackRoleConfig =
        (config.iamRoleCustomizationJSON && config.iamRoleCustomizationJSON.vamsStacks) || {};

    iam.Role.customizeRoles(app, {
        // Discovery mode keeps roles in the template while still writing the report;
        // otherwise role synthesis is prevented and unmapped roles fail synthesis.
        preventSynthesis: vamsStacks.generateReportOnly ? false : true,
        usePrecreatedRoles: vamsStacks.precreatedRoles || {},
    });
}
