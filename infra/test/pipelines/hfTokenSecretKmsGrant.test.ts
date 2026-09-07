/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A Lambda that writes a KMS-encrypted secret must hold a KMS grant on that secret's key.
 *
 * MEASURED, on a fresh deployment of `vams`/`prod16` into us-east-1 with
 * `app.useKmsCmkEncryption.enabled` — the five NVIDIA pipelines each populate a HuggingFace token secret
 * through a custom resource, and every one of them failed:
 *
 *   AccessDeniedException ... calling the PutSecretValue operation: Access to KMS is not allowed
 *
 * which cancelled its siblings and rolled the entire core stack back. Secrets Manager encrypts the value
 * with the secret's own key, so `secretsmanager:PutSecretValue` alone is never enough for a CMK-encrypted
 * secret.
 *
 * WHY IT WAS NOT CAUGHT EARLIER, which is the part worth pinning. `Secret.grantWrite` normally adds the
 * KMS grant for you — it delegates to `Key.grantEncryptDecrypt`. But these secrets take their key via
 * `kms.Key.fromKeyArn`, which is itself deliberate: passing the key OBJECT would write this pipeline
 * stack's role into the key's resource policy, and the key lives in the storage nested stack, so the two
 * stacks would reference each other and AWS CloudFormation would reject the changeset with a circular
 * dependency (infra/CLAUDE.md, KMS trap 2). For an ARN-imported key CDK cannot see the policy and adds no
 * principal-side statement, so the two correct decisions combined to produce no grant at all.
 *
 * The assertion is therefore on the SYNTHESIZED policy rather than on the source: both `grantWrite` and an
 * explicit statement look the same in TypeScript, and only the template says which actions were emitted.
 */

import { synthTemplate, SynthResult, TemplateName } from "../support/templateSynth";

/** Writing a value to a CMK-encrypted secret needs a data key generated under that CMK. */
const REQUIRED_KMS_ACTION = /^kms:GenerateDataKey\*?$/;

function actionsOf(statement: any): string[] {
    return ([] as string[]).concat(statement.Action ?? []);
}

/** Every IAM policy attached to a HuggingFace-token secret-populate Lambda's own role. */
function populatePolicies(s: SynthResult) {
    return s.ofType("AWS::IAM::Policy").filter(
        (p) =>
            /HfTokenSecretPopulate/i.test(p.logicalId) &&
            // The provider framework's role invokes the handler; the handler's own role is the one
            // that calls PutSecretValue, and it is the one that needs the key.
            !/Providerframework/i.test(p.logicalId)
    );
}

describe.each(["commercial", "govcloud", "eusovereign"] as TemplateName[])(
    "%s: HuggingFace token secret KMS grant",
    (templateName) => {
        let synth: SynthResult;

        beforeAll(() => {
            synth = synthTemplate(templateName, {
                mutateKey: "nvidia-on-with-cmk",
                mutate: (c: any) => {
                    // The defect needs a customer-managed key: with CMK off the secret uses the
                    // AWS-managed Secrets Manager key, which needs no explicit grant, and every
                    // assertion here would pass while the CMK path stayed broken.
                    c.app.useKmsCmkEncryption = {
                        ...(c.app.useKmsCmkEncryption ?? {}),
                        enabled: true,
                    };
                    c.app.useGlobalVpc = { ...(c.app.useGlobalVpc ?? {}), enabled: true };
                    const pipelines = c.app?.pipelines ?? {};
                    for (const key of ["useNvidiaCosmos", "useNvidiaCosmos3", "useNvidiaGr00t"]) {
                        if (pipelines[key]) {
                            pipelines[key].enabled = true;
                            pipelines[key].huggingFaceToken = "synth-only-token";
                            if ("useCodeBuild" in pipelines[key]) {
                                pipelines[key].useCodeBuild = true;
                            }
                        }
                    }
                },
            });
        });

        it("[control] the assembly contains secret-populate policies at all", () => {
            // Without this every assertion below is satisfied by an assembly that emitted none — and
            // these exist only when an NVIDIA pipeline is enabled, which the shipped templates do not do.
            expect(populatePolicies(synth).length).toBeGreaterThan(0);
        });

        it("[control] a CMK is actually in use, so the grant is genuinely required", () => {
            // With CMK off the secret has no encryptionKey and the fix is a no-op, so the mutation above
            // is load-bearing. Asserted rather than assumed.
            expect(synth.ofType("AWS::KMS::Key").length).toBeGreaterThan(0);
        });

        it("every secret-populate Lambda can generate a data key under the secret's CMK", () => {
            const offenders: string[] = [];
            for (const policy of populatePolicies(synth)) {
                const statements = policy.properties.PolicyDocument?.Statement ?? [];
                const granted = statements.some((st: any) =>
                    actionsOf(st).some((a) => REQUIRED_KMS_ACTION.test(a))
                );
                if (!granted) {
                    const seen = statements.flatMap(actionsOf).join(", ");
                    offenders.push(
                        `${policy.stack}/${policy.logicalId} has no kms:GenerateDataKey* — it can call ` +
                            `PutSecretValue but Secrets Manager cannot encrypt the value, so the custom ` +
                            `resource fails with "Access to KMS is not allowed" and rolls the stack ` +
                            `back. Actions present: ${seen}`
                    );
                }
            }
            expect(offenders).toEqual([]);
        });

        it("the KMS grant is principal-side, not written into the key's resource policy", () => {
            // The other half of the constraint. Granting through the key OBJECT would put this pipeline
            // stack's role into the storage stack's key policy and make the two stacks circular, which
            // AWS CloudFormation rejects at changeset creation rather than at synth.
            const keyPolicyPrincipals = synth
                .ofType("AWS::KMS::Key")
                .flatMap((k) => k.properties.KeyPolicy?.Statement ?? [])
                .map((st: any) => JSON.stringify(st.Principal ?? {}));
            const leaked = keyPolicyPrincipals.filter((p) => /HfTokenSecretPopulate/i.test(p));
            expect(leaked).toEqual([]);
        });
    }
);
