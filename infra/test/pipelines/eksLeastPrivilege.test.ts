/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Neither of the RapidPipeline EKS identities may hold more than the job it runs needs.
 *
 * Two over-grants, both of which made a narrower grant elsewhere in the same construct decorative:
 *
 *  1. **The node group role carried the asset-bucket grants** — `s3:GetObject`/`PutObject`/`ListBucket`
 *     on every registered asset bucket and the auxiliary bucket, plus (new in this release)
 *     `grantExternalAssetBucketKmsKeys`, which reaches cross-account customer-managed keys. A node role
 *     is readable from every pod on the node through the instance metadata endpoint, and the pod here
 *     runs third-party 3D-conversion code over operator-supplied files. The same permissions were
 *     already on the IRSA role for the `rapid-pipeline-sa` service account, which is what the pods
 *     actually use — so the node copy added blast radius and nothing else.
 *
 *  2. **The pipeline Lambda was mapped into `system:masters`** — cluster-admin, for a handler that
 *     creates one Job and reads its pods. Alongside it, `eks:*`-style actions on `Resource: "*"` and an
 *     `iam:PassRole` nothing passes a role with.
 *
 * Three things are asserted as positive controls rather than left implied, because each removal is a
 * negative claim that a synth missing the resource would satisfy just as well:
 *
 *  * the three roles are present in the synth at all;
 *  * the IRSA role still HAS the S3 and KMS grants — the capability moved, it did not vanish, and a
 *    test that only checked the node role would pass if both had been stripped and the pipeline broken;
 *  * the aws-auth ConfigMap still maps the handler role, to the scoped group. Asserting only "no
 *    system:masters" would be satisfied by a mapping that was deleted outright, which locks the handler
 *    out of the cluster.
 *
 * The `iam:PassRole` assertion is scoped to the handler's own role on purpose: aws-cdk-lib's cluster
 * creation role legitimately passes the cluster role, so a stack-wide check would fail on CDK's own
 * resource and have to be loosened until it proved nothing.
 */

import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

/**
 * Enables the EKS pipeline and the VPC it requires. Marketplace container URI is a placeholder.
 *
 * A CMK is switched on deliberately. The commercial template ships `useKmsCmkEncryption` disabled, so
 * `storageResources.encryption.kmsKey` is undefined and NEITHER role receives a `kms:` grant — which
 * would make "the node role grants no kms action" pass without a key existing to grant, and would make
 * the IRSA positive control impossible to state. With a key present both claims are about the same
 * synth: the key is granted to the service account and withheld from the node.
 */
function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.useKmsCmkEncryption.enabled = true;
    c.app.pipelines.useRapidPipeline.enabled = true;
    c.app.pipelines.useRapidPipeline.autoRegisterWithVAMS = false;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

/** The kubectl-applied manifests, decoded from the custom resource that carries them. */
function manifests(synth: SynthResult): any[] {
    return synth.resources
        .filter((r) => r.type === "Custom::AWSCDK-EKS-KubernetesResource")
        .flatMap((r) => {
            try {
                return JSON.parse(SynthResult.flatten((r.properties as any).Manifest ?? "[]"));
            } catch {
                // A manifest carrying an unresolved token is not one of the ones under test.
                return [];
            }
        });
}

describe("RapidPipeline EKS least privilege", () => {
    let synth: SynthResult;

    /**
     * Every IAM statement that applies to a role, gathered across all three places CDK can put one.
     * Scanning only `AWS::IAM::Policy` under-reports: once a role's inline policy exceeds the 10 KB
     * limit CDK spills the rest into an `AWS::IAM::ManagedPolicy`, so a grant can be present and
     * invisible to a narrower scan.
     */
    function statementsForRole(rolePattern: RegExp): any[] {
        const roleIds = synth
            .ofType("AWS::IAM::Role")
            .filter((r) => rolePattern.test(r.logicalId))
            .map((r) => r.logicalId);
        expect(roleIds.length).toBeGreaterThan(0);

        const attachedTo = (p: Resource) =>
            (((p.properties as any).Roles ?? []) as unknown[]).some((ref) =>
                roleIds.some(
                    (id) => SynthResult.flatten(ref) === id || JSON.stringify(ref).includes(id)
                )
            );

        const fromPolicies = synth.resources
            .filter((r) => /IAM::(Policy|ManagedPolicy)$/.test(r.type) && attachedTo(r))
            .flatMap((p) => ((p.properties as any).PolicyDocument?.Statement ?? []) as any[]);

        const fromInline = synth
            .ofType("AWS::IAM::Role")
            .filter((r) => roleIds.includes(r.logicalId))
            .flatMap((r) => ((r.properties as any).Policies ?? []) as any[])
            .flatMap((p) => (p.PolicyDocument?.Statement ?? []) as any[]);

        return [...fromPolicies, ...fromInline];
    }

    const actionsOf = (statements: any[]): string[] =>
        statements.flatMap((s) =>
            (Array.isArray(s.Action) ? s.Action : [s.Action]).filter(Boolean)
        );

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-pipeline-cmk-enabled",
        });
    });

    test("the three roles under test ARE in this synth", () => {
        // The control. The pipeline ships disabled, so without it every "does not grant X" assertion
        // below is satisfied by a template containing none of these roles.
        const roles = synth.ofType("AWS::IAM::Role").map((r) => r.logicalId);
        expect(roles.some((id) => /NodeGroupRole/.test(id))).toBe(true);
        expect(roles.some((id) => /PipelineServiceAccount/i.test(id))).toBe(true);
        expect(roles.some((id) => /ConsolidatedHandlerServiceRole/.test(id))).toBe(true);
    });

    describe("the node group role holds no asset-data permissions", () => {
        test("it grants no s3 action", () => {
            const actions = actionsOf(statementsForRole(/NodeGroupRole/));
            expect(actions.filter((a) => a.startsWith("s3:"))).toEqual([]);
        });

        test("it grants no kms action", () => {
            // This is the half the release widened: grantExternalAssetBucketKmsKeys reaches
            // customer-managed keys on buckets in other accounts.
            const actions = actionsOf(statementsForRole(/NodeGroupRole/));
            expect(actions.filter((a) => a.startsWith("kms:"))).toEqual([]);
        });

        test("it still carries what the kubelet needs to join and pull images", () => {
            // The other side of the removal: strip too much and the node never registers, which shows
            // up as a node group that never reaches ACTIVE rather than as a permissions error.
            const role = synth
                .ofType("AWS::IAM::Role")
                .find((r) => /NodeGroupRole/.test(r.logicalId))!;
            const managed = ((role.properties as any).ManagedPolicyArns ?? []).map((a: unknown) =>
                SynthResult.flatten(a)
            );
            for (const required of [
                "AmazonEKSWorkerNodePolicy",
                "AmazonEKS_CNI_Policy",
                "AmazonEC2ContainerRegistryReadOnly",
            ]) {
                expect(managed.join(" ")).toContain(required);
            }
        });
    });

    test("the IRSA service-account role still HAS the S3 and KMS grants", () => {
        // The positive control for the removal above. Without it, stripping both roles — which breaks
        // the pipeline outright — would satisfy every assertion in this file.
        const actions = actionsOf(statementsForRole(/PipelineServiceAccount/i));
        expect(actions.some((a) => a === "s3:GetObject")).toBe(true);
        expect(actions.some((a) => a === "s3:PutObject")).toBe(true);
        expect(actions.some((a) => a.startsWith("kms:"))).toBe(true);
    });

    describe("the pipeline Lambda is not a cluster administrator", () => {
        test("no role is mapped into system:masters", () => {
            const awsAuth = manifests(synth).find(
                (m) => m?.kind === "ConfigMap" && m?.metadata?.name === "aws-auth"
            );
            expect(awsAuth).toBeDefined();
            expect(JSON.stringify(awsAuth.data)).not.toContain("system:masters");
        });

        test("the handler role is still mapped, to the scoped group", () => {
            // Asserting only the absence above would be satisfied by deleting the mapping, which locks
            // the handler out of the cluster entirely.
            const awsAuth = manifests(synth).find(
                (m) => m?.kind === "ConfigMap" && m?.metadata?.name === "aws-auth"
            );
            const mapRoles = JSON.parse(awsAuth.data.mapRoles);
            const handler = mapRoles.find((m: any) => m.username === "pipeline-lambda");
            expect(handler).toBeDefined();
            expect(handler.groups).toEqual(["vams-rapid-pipeline"]);
            expect(handler.rolearn).toContain("ConsolidatedHandlerServiceRole");
        });

        test("the group is bound by a namespaced Role, not a cluster-wide one", () => {
            // A ClusterRole granting the same verbs would satisfy a rules-only check while making the
            // handler able to read every pod and every job in the cluster.
            const kinds = manifests(synth)
                .filter((m) => JSON.stringify(m).includes("vams-rapid-pipeline"))
                .map((m) => m.kind);
            expect(kinds).not.toContain("ClusterRole");
            expect(kinds).not.toContain("ClusterRoleBinding");
            expect(kinds).toContain("Role");
            expect(kinds).toContain("RoleBinding");
        });

        test("the Role grants exactly the verbs the handler calls", () => {
            const role = manifests(synth).find(
                (m) => m?.kind === "Role" && m?.metadata?.name === "vams-rapid-pipeline-job-runner"
            );
            expect(role).toBeDefined();
            expect(role.metadata.namespace).toBe("default");

            // Compared as a set rather than a superset: the point of the finding is that a wider grant
            // is the defect, so an extra rule has to fail this.
            const normalize = (rules: any[]) =>
                rules
                    .map(
                        (r) =>
                            `${[...r.apiGroups].sort().join("|")}/${[...r.resources]
                                .sort()
                                .join("|")}:${[...r.verbs].sort().join(",")}`
                    )
                    .sort();

            expect(normalize(role.rules)).toEqual(
                normalize([
                    {
                        apiGroups: ["batch"],
                        resources: ["jobs"],
                        verbs: ["create", "get", "delete"],
                    },
                    { apiGroups: ["batch"], resources: ["jobs/status"], verbs: ["get"] },
                    { apiGroups: [""], resources: ["pods"], verbs: ["get", "list"] },
                    { apiGroups: [""], resources: ["pods/log"], verbs: ["get"] },
                    { apiGroups: [""], resources: ["events"], verbs: ["list"] },
                ])
            );
        });

        test("the RoleBinding ties that group to that Role in the same namespace", () => {
            // The link. Either half alone grants nothing, and a binding that named the wrong Role or
            // landed in another namespace would leave the handler with no access at all.
            const binding = manifests(synth).find(
                (m) =>
                    m?.kind === "RoleBinding" &&
                    m?.metadata?.name === "vams-rapid-pipeline-job-runner"
            );
            expect(binding).toBeDefined();
            expect(binding.metadata.namespace).toBe("default");
            expect(binding.roleRef).toMatchObject({
                kind: "Role",
                name: "vams-rapid-pipeline-job-runner",
            });
            expect(binding.subjects).toEqual([
                {
                    kind: "Group",
                    name: "vams-rapid-pipeline",
                    apiGroup: "rbac.authorization.k8s.io",
                },
            ]);
        });
    });

    describe("the pipeline Lambda's AWS permissions are scoped", () => {
        test("no eks action is granted on a resource wildcard", () => {
            const statements = statementsForRole(/ConsolidatedHandlerServiceRole/);
            const eksOnWildcard = statements.filter((s) => {
                const actions = (Array.isArray(s.Action) ? s.Action : [s.Action]).filter(Boolean);
                const resources = (Array.isArray(s.Resource) ? s.Resource : [s.Resource]).filter(
                    Boolean
                );
                return (
                    actions.some((a: string) => a.startsWith("eks:")) &&
                    resources.some((r: unknown) => r === "*")
                );
            });
            expect(eksOnWildcard).toEqual([]);
        });

        test("the eks actions it does hold are only the ones it calls", () => {
            const actions = actionsOf(statementsForRole(/ConsolidatedHandlerServiceRole/)).filter(
                (a) => a.startsWith("eks:")
            );
            expect([...actions].sort()).toEqual([
                "eks:AccessKubernetesApi",
                "eks:DescribeCluster",
                "eks:ListAccessEntries",
            ]);
        });

        test("it holds no iam:PassRole", () => {
            // Scoped to this role: aws-cdk-lib's own cluster creation role passes the cluster role, so
            // a stack-wide assertion would fail on CDK's resource and have to be weakened.
            const actions = actionsOf(statementsForRole(/ConsolidatedHandlerServiceRole/));
            expect(actions).not.toContain("iam:PassRole");
        });
    });
});
