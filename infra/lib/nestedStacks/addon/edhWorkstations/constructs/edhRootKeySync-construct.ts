/*
 * The EDH Workstations add-on's automatic root key sync.
 *
 * EDH regenerates its web interface root key each time the web interface starts, including after every
 * controller reboot, and publishes it nowhere. With `edhRootKeyAutoSync` on, this construct gives the sync
 * function (`buildEdhRootKeySyncFunction`) what it needs to copy the current key from the EDH controller
 * into the root-key secret, and nothing more:
 *
 *   * a VAMS-owned Run Command document whose one step encrypts the cluster's key file to the public key it
 *     is passed (its only parameter, base64 characters only) and prints the base64 ciphertext, so the
 *     command history never holds the key. The function generates that RSA key pair for each run and keeps
 *     the private half in the run's memory only, so the deployment creates no key and no role can decrypt
 *     the ciphertext the history keeps;
 *   * the sync role's grants, each scoped to the one resource it names.
 *
 * It creates no CloudWatch alarm. Each run logs its outcome and writes the `RootKeySyncFailures` embedded
 * metric (the backend's `METRIC_NAMESPACE`, `METRIC_FAILURES`, `METRIC_DIMENSION`), which an operator may alarm on.
 *
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import * as crypto from "crypto";
import { readFileSync } from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../../config/config";
import { Service } from "../../../../helper/service-helper";

/** The document's one step, in the repository; comment and blank lines are dropped when it is built. */
export const EDH_ROOT_KEY_SCRIPT = path.join(
    __dirname,
    "../../../../../../backendAddons/edh/rootKeySync/read-root-key.sh"
);
/** Where the script names the key file; replaced with the configured cluster's key file. */
export const EDH_ROOT_KEY_FILE_PLACEHOLDER = "__EDH_ROOT_KEY_FILE__";
/** The document version the sync runs: the one the deployment creates. A replacement creates version 1 again. */
export const EDH_ROOT_KEY_DOCUMENT_VERSION = "1";
/**
 * The publicKey parameter's allowedPattern: base64 characters only, so nothing else reaches the shell. Systems
 * Manager parses it as an RE2 expression, whose repeat counts stop at 1000; the RSA_3072 key's public half
 * (SubjectPublicKeyInfo, 422 bytes) is 564 base64 characters.
 */
export const EDH_ROOT_KEY_PUBLIC_KEY_PATTERN = "^[A-Za-z0-9+/]{1,1000}={0,2}$";
/** The session-table row the sync's rate limit is held on (the backend's `ROOT_KEY_SYNC_SETTINGS_KEY`). */
export const EDH_ROOT_KEY_SYNC_SETTINGS_KEY = "SETTINGS#rootKeySync";
/** The environment variable that names the sync function to the add-on Lambdas that call EDH. */
export const EDH_ROOT_KEY_SYNC_FUNCTION_ENV = "EDH_ROOT_KEY_SYNC_FUNCTION_NAME";
/** The tags EDH's controller carries: EDH's node type, and the stack name CloudFormation sets. */
export const EDH_CONTROLLER_NODE_TYPE_TAG = "edh:NodeType";
export const EDH_CONTROLLER_NODE_TYPE = "controller";
export const EDH_CLUSTER_STACK_NAME_TAG = "aws:cloudformation:stack-name";
// The cluster id becomes a path segment of the command; getConfig() holds it to a stack name already.
const CLUSTER_STACK_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9-]*$/;
/** The EDH root key file on the controller of the cluster `edhClusterId`. */
export function edhRootKeyFile(edhClusterId: string): string {
    return `/opt/edh/${edhClusterId}/cluster_manager/web_interface/keys/admin_api_key.txt`;
}

/** The document step's command lines for the cluster: the script's lines, key file substituted. */
export function edhRootKeyCommandLines(edhClusterId: string): string[] {
    if (!CLUSTER_STACK_NAME_PATTERN.test(edhClusterId)) {
        throw new Error(
            `The EDH root key sync needs a cluster id that is a CloudFormation stack name. Got: ${JSON.stringify(
                edhClusterId
            )}`
        );
    }
    const keyFile = edhRootKeyFile(edhClusterId);
    return readFileSync(EDH_ROOT_KEY_SCRIPT, "utf8")
        .split(/\r?\n/)
        .filter((line) => line.trim() !== "" && !line.trimStart().startsWith("#"))
        .map((line) => line.split(EDH_ROOT_KEY_FILE_PLACEHOLDER).join(keyFile));
}

export interface EdhRootKeySyncProps {
    config: Config.Config;
    syncFunction: lambda.Function;
    rootKeySecret: secretsmanager.ISecret;
    sessionTable: dynamodb.ITable;
}

export class EdhRootKeySyncConstruct extends Construct {
    public readonly document: ssm.CfnDocument;
    private readonly syncFunction: lambda.Function;

    constructor(scope: Construct, id: string, props: EdhRootKeySyncProps) {
        super(scope, id);
        const clusterId = props.config.app.addons.useEdhWorkstations.edhClusterId;
        const fun = props.syncFunction;
        const role = fun.role as iam.IRole;
        const stack = cdk.Stack.of(this);
        this.syncFunction = fun;

        this.document = new ssm.CfnDocument(this, "ReadRootKeyDocument", {
            documentType: "Command",
            // A content change replaces the document, so the version the sync pins is always the deployed one.
            updateMethod: "Replace",
            content: {
                schemaVersion: "2.2",
                description:
                    "VAMS EDH root key sync: prints the EDH root key of this cluster encrypted to the given RSA public key (RSAES-OAEP, SHA-256), never the key itself",
                parameters: {
                    publicKey: {
                        type: "String",
                        description:
                            "Base64 DER public key (SubjectPublicKeyInfo) of the RSA key the VAMS root key sync generates for the run",
                        allowedPattern: EDH_ROOT_KEY_PUBLIC_KEY_PATTERN,
                    },
                },
                mainSteps: [
                    {
                        action: "aws:runShellScript",
                        name: "encryptRootKey",
                        precondition: { StringEquals: ["platformType", "Linux"] },
                        inputs: {
                            timeoutSeconds: "30",
                            runCommand: edhRootKeyCommandLines(clusterId),
                        },
                    },
                ],
            },
        });

        fun.addEnvironment("EDH_ROOT_KEY_DOCUMENT_NAME", this.document.ref);
        fun.addEnvironment("EDH_ROOT_KEY_DOCUMENT_VERSION", EDH_ROOT_KEY_DOCUMENT_VERSION);
        // The function confirms the pinned version's content is this step before it sends the document,
        // and sends that version's hash, so a document recreated under the same name is never run.
        fun.addEnvironment(
            "EDH_ROOT_KEY_DOCUMENT_STEP_SHA256",
            crypto
                .createHash("sha256")
                .update(edhRootKeyCommandLines(clusterId).join("\n"))
                .digest("hex")
        );

        // Run Command authorizes the document and the instance separately, so the instance's tag condition
        // sits on a statement of its own: the document carries no such tags.
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["ssm:SendCommand", "ssm:DescribeDocument", "ssm:GetDocument"],
                resources: [
                    stack.formatArn({
                        service: "ssm",
                        resource: "document",
                        resourceName: this.document.ref,
                        arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
                    }),
                ],
            })
        );
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["ssm:SendCommand"],
                resources: [
                    stack.formatArn({
                        service: "ec2",
                        resource: "instance",
                        resourceName: "*",
                        arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
                    }),
                ],
                conditions: {
                    StringEquals: {
                        [`ssm:resourceTag/${EDH_CONTROLLER_NODE_TYPE_TAG}`]:
                            EDH_CONTROLLER_NODE_TYPE,
                        [`ssm:resourceTag/${EDH_CLUSTER_STACK_NAME_TAG}`]: clusterId,
                    },
                },
            })
        );
        // Neither read accepts a resource ARN or a resource-level condition.
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["ssm:GetCommandInvocation", "ec2:DescribeInstances"],
                resources: ["*"],
            })
        );
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["ssm:GetParameter"],
                resources: [
                    stack.formatArn({
                        service: "ssm",
                        resource: "parameter",
                        resourceName: `edh/${clusterId}/configuration/ControllerInstanceId`,
                        arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
                    }),
                ],
            })
        );
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"],
                resources: [props.rootKeySecret.secretArn],
            })
        );
        // A secret under a customer managed key: the key may be used only through Secrets Manager and only
        // for the root-key secret. Under the AWS managed key this statement is not needed and grants nothing.
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["kms:Decrypt", "kms:GenerateDataKey"],
                resources: [
                    stack.formatArn({
                        service: "kms",
                        resource: "key",
                        resourceName: "*",
                        arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
                    }),
                ],
                conditions: {
                    StringEquals: {
                        "kms:ViaService": Service("SECRETSMANAGER", false).Endpoint,
                        "kms:EncryptionContext:SecretARN": props.rootKeySecret.secretArn,
                    },
                },
            })
        );
        // The rate limit: one conditional write on the one reserved row.
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["dynamodb:UpdateItem"],
                resources: [props.sessionTable.tableArn],
                conditions: {
                    "ForAllValues:StringEquals": {
                        "dynamodb:LeadingKeys": [EDH_ROOT_KEY_SYNC_SETTINGS_KEY],
                    },
                },
            })
        );
        // AwsSolutions-IAM5 reads the resource ARN and not the condition, so each suppression names its one
        // ARN shape, on this role only.
        NagSuppressions.addResourceSuppressions(
            fun,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: `ssm:SendCommand on the instances of this account and Region that carry EDH's controller tags for the configured cluster (ssm:resourceTag/${EDH_CONTROLLER_NODE_TYPE_TAG} = ${EDH_CONTROLLER_NODE_TYPE} and ssm:resourceTag/${EDH_CLUSTER_STACK_NAME_TAG} = the configured edhClusterId); the controller's instance id changes when EDH replaces it. The only document the role may send is the root key sync's own.`,
                    appliesTo: [
                        {
                            regex: "/^Resource::arn:<AWS::Partition>:ec2:[^:]+:[^:]+:instance\\/\\*$/g",
                        },
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ssm:GetCommandInvocation and ec2:DescribeInstances accept no resource ARN or resource-level condition; the root key sync reads its own command's invocation and the controller instance EDH publishes. Both are reads.",
                    appliesTo: [{ regex: "/^Resource::\\*$/g" }],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "kms:Decrypt and kms:GenerateDataKey on the account's KMS keys only through Secrets Manager (kms:ViaService) and only for the root-key secret (kms:EncryptionContext:SecretARN), for a secret encrypted under a customer managed key whose ARN the configuration does not name.",
                    appliesTo: [
                        { regex: "/^Resource::arn:<AWS::Partition>:kms:[^:]+:[^:]+:key\\/\\*$/g" },
                    ],
                },
            ],
            true
        );
    }

    /** Lets an add-on Lambda that calls EDH request a sync when EDH rejects the key the secret holds. */
    public grantTrigger(caller: lambda.Function): void {
        caller.addEnvironment(EDH_ROOT_KEY_SYNC_FUNCTION_ENV, this.syncFunction.functionName);
        this.syncFunction.grantInvoke(caller);
    }
}
