/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as bedrock from "aws-cdk-lib/aws-bedrock";
import * as kms from "aws-cdk-lib/aws-kms";
import * as crypto from "crypto";
import { Construct } from "constructs";
import * as Config from "../../../../../../config/config";
import { generateUniqueNameHash } from "../../../../../helper/security";
import { Service } from "../../../../../helper/service-helper";

/**
 * The one guardrail the pipeline's two analysis functions send every Converse call with: its id and
 * version for the `BEDROCK_GUARDRAIL_IDENTIFIER` / `BEDROCK_GUARDRAIL_VERSION` environment and its ARN
 * for the `bedrock:ApplyGuardrail` grant. Deploy-time attributes of the created guardrail, or literals
 * composed from an operator-owned one.
 */
export interface SystemGenAiGuardrailReference {
    identifier: string;
    version: string;
    arn: string;
}

/**
 * The PII and credential entity types the created guardrail's sensitive-information policy names:
 * the personal identifiers of the organizational Bedrock guardrail guidance plus the secrets a text or
 * configuration file uploaded as an asset may carry.
 */
export const SYSTEM_GENAI_GUARDRAIL_PII_ENTITIES = [
    "EMAIL",
    "PHONE",
    "NAME",
    "ADDRESS",
    "US_SOCIAL_SECURITY_NUMBER",
    "CREDIT_DEBIT_CARD_NUMBER",
    "AWS_ACCESS_KEY",
    "AWS_SECRET_KEY",
    "PASSWORD",
] as const;

/** The messages Amazon Bedrock substitutes for a blocked prompt and a blocked model response. */
export const SYSTEM_GENAI_GUARDRAIL_BLOCKED_INPUT_MESSAGE =
    "The analysis prompt was blocked by the VAMS Bedrock guardrail.";
export const SYSTEM_GENAI_GUARDRAIL_BLOCKED_OUTPUT_MESSAGE =
    "The analysis response was blocked by the VAMS Bedrock guardrail.";

/** The guardrail name prefix; the deployment hash after it keeps two deployments in one account apart. */
export const SYSTEM_GENAI_GUARDRAIL_NAME_PREFIX = "VAMS-SystemGenAiMetadata-";

export interface SystemGenAiGuardrailConstructProps {
    config: Config.Config;
    /** The deployment key; `undefined` leaves the guardrail on the AWS-managed Bedrock key. */
    kmsKey?: kms.IKey;
}

/**
 * The Amazon Bedrock guardrail the deployment creates for the SYSTEM GenAI metadata pipeline when
 * `bedrockGuardrail.create.enabled` is set: a PROMPT_ATTACK content filter on the prompt at the
 * configured strength (the response side is NONE, as Bedrock requires for that filter) and, unless
 * `piiFilter` is `off`, one sensitive-information filter per entity in
 * {@link SYSTEM_GENAI_GUARDRAIL_PII_ENTITIES} that anonymizes or blocks on the prompt and on the response
 * alike. A published version follows the
 * DRAFT, and the version's description carries a digest of the policy so a changed strength or PII
 * treatment publishes a new version instead of leaving the functions on the old one.
 *
 * The name is required by the API and is composed from the deployment (core stack name, account) with a
 * literal identifier, so it is stable across synths and unique per deployment in an account and Region.
 * An explicitly named resource: an orphaned copy collides with a redeploy of the same configuration.
 */
export class SystemGenAiGuardrailConstruct extends Construct {
    public readonly guardrail: bedrock.CfnGuardrail;
    public readonly version: bedrock.CfnGuardrailVersion;
    public readonly reference: SystemGenAiGuardrailReference;

    constructor(parent: Construct, name: string, props: SystemGenAiGuardrailConstructProps) {
        super(parent, name);

        const create = props.config.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail.create;

        const contentPolicyConfig: bedrock.CfnGuardrail.ContentPolicyConfigProperty = {
            filtersConfig: [
                {
                    type: "PROMPT_ATTACK",
                    inputStrength: create.promptAttackInputStrength,
                    outputStrength: "NONE",
                },
            ],
        };

        // The legacy `action` alone is applied to the model response; the prompt needs its own
        // `inputAction` (with the side enabled) for the filter to mask or block the PII an uploaded file
        // carries before the model reads it. Both sides carry the configured action.
        const piiAction = create.piiFilter === "block" ? "BLOCK" : "ANONYMIZE";
        const sensitiveInformationPolicyConfig:
            | bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty
            | undefined =
            create.piiFilter === "off"
                ? undefined
                : {
                      piiEntitiesConfig: SYSTEM_GENAI_GUARDRAIL_PII_ENTITIES.map((type) => ({
                          type: type,
                          action: piiAction,
                          inputAction: piiAction,
                          inputEnabled: true,
                          outputAction: piiAction,
                          outputEnabled: true,
                      })),
                  };

        this.guardrail = new bedrock.CfnGuardrail(this, "Guardrail", {
            name:
                SYSTEM_GENAI_GUARDRAIL_NAME_PREFIX +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "SystemGenAiMetadata-BedrockGuardrail",
                    10
                ),
            description:
                "Screens the SYSTEM GenAI metadata pipeline's analysis prompts for prompt attacks" +
                (sensitiveInformationPolicyConfig ? " and PII." : "."),
            blockedInputMessaging: SYSTEM_GENAI_GUARDRAIL_BLOCKED_INPUT_MESSAGE,
            blockedOutputsMessaging: SYSTEM_GENAI_GUARDRAIL_BLOCKED_OUTPUT_MESSAGE,
            contentPolicyConfig: contentPolicyConfig,
            sensitiveInformationPolicyConfig: sensitiveInformationPolicyConfig,
            kmsKeyArn: props.kmsKey?.keyArn,
        });

        // The policy digest in the description: AWS::Bedrock::GuardrailVersion is immutable, so a new
        // description replaces it, publishing a version that carries the changed policy.
        const policyDigest = crypto
            .createHash("sha1")
            .update(JSON.stringify({ contentPolicyConfig, sensitiveInformationPolicyConfig }))
            .digest("hex")
            .substring(0, 10);
        this.version = new bedrock.CfnGuardrailVersion(this, "GuardrailVersion", {
            guardrailIdentifier: this.guardrail.attrGuardrailId,
            description: `VAMS SYSTEM GenAI metadata guardrail policy ${policyDigest}`,
        });

        this.reference = {
            identifier: this.guardrail.attrGuardrailId,
            version: this.version.attrVersion,
            arn: this.guardrail.attrGuardrailArn,
        };
    }
}

/**
 * The guardrail reference the analysis functions are built with: the created guardrail's attributes
 * when `create.enabled`, the operator-owned guardrail's id, version and composed ARN when the
 * `guardrailIdentifier` pair is set, and `undefined` when the pipeline runs without one. Configuration
 * validation rejects both at once.
 */
export function resolveSystemGenAiGuardrail(
    scope: Construct,
    config: Config.Config,
    kmsKey?: kms.IKey
): SystemGenAiGuardrailReference | undefined {
    const guardrail = config.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail;
    if (guardrail.create.enabled) {
        return new SystemGenAiGuardrailConstruct(scope, "SystemGenAiMetadataGuardrail", {
            config: config,
            kmsKey: kmsKey,
        }).reference;
    }
    if (guardrail.guardrailIdentifier !== "") {
        return {
            identifier: guardrail.guardrailIdentifier,
            version: guardrail.guardrailVersion,
            // arn:<partition>:bedrock:<region>:<account>:guardrail/<identifier>
            arn: Service("BEDROCK").ARN("guardrail", guardrail.guardrailIdentifier),
        };
    }
    return undefined;
}
