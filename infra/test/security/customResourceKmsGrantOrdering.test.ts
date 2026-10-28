/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A custom resource that reads or writes KMS-encrypted storage runs only after the IAM policy that
 * grants its role the key.
 *
 * With `app.useKmsCmkEncryption.enabled` turned on for an existing deployment, one stack update
 * re-encrypts the tables under the new key, creates the policy that grants the seed roles that key, and
 * re-runs the seed custom resources. CloudFormation orders resources only by the dependency graph, so a
 * policy attached to the role from outside the role (`role.attachInlinePolicy(new iam.Policy(...))`) is
 * created in parallel with a custom resource that runs as the role. The custom resource reaches the
 * role through its provider function, and the function depends on the role only: on an update the
 * function already exists, the seed fires at once, and `kms:Decrypt` is denied. That is how the
 * auth-defaults seeds failed (issue #841), and the metadata-schema seeds have the same shape.
 *
 * The dependency belongs on the custom resource, not on the provider function. Creating the function
 * needs no access to the key; invoking it does. This is also what `AwsCustomResource` does itself for
 * the policy it builds from its `policy` prop.
 *
 * Two levels of assertion:
 *
 *   1. The two seed roles that take their key grant from a separately attached policy: every custom
 *      resource that runs as the role carries `DependsOn` on that policy, when the CMK is on.
 *   2. Every custom resource in every template: each policy that grants a KMS action to the role its
 *      code runs as (the provider function's role, and for a `cr.Provider` the user handler's role) is
 *      one of its ancestors in the dependency graph, or grants nothing an ancestor policy on that role
 *      does not. A grant added with `addToRolePolicy` lands in the role's default policy, which the
 *      function itself depends on, so it passes through the function; a separately attached policy
 *      passes only with an explicit dependency. The second clause covers `AwsCustomResource`s that
 *      share one singleton provider role, where each depends on its own copy of the key statement.
 *
 * The CMK-off synthesis is the control: the same custom resources exist, and no policy grants their
 * roles the key, so there is nothing to wait for.
 */

import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

const withCmk = () =>
    synthTemplate("commercial", {
        mutate: (c) => {
            c.app.useKmsCmkEncryption.enabled = true;
        },
        mutateKey: "kms-cmk-on",
    });
const withoutCmk = () => synthTemplate("commercial"); // useKmsCmkEncryption.enabled = false

const CUSTOM_RESOURCE = (type: string) =>
    type.startsWith("Custom::") || type === "AWS::CloudFormation::CustomResource";

const asArray = <T>(value: T | T[] | undefined): T[] =>
    value === undefined ? [] : Array.isArray(value) ? value : [value];

/** The logical id a `Ref` or `Fn::GetAtt` points at, or undefined for anything else. */
function referencedId(value: any): string | undefined {
    if (value && typeof value === "object") {
        if (typeof value.Ref === "string") return value.Ref;
        if (Array.isArray(value["Fn::GetAtt"])) return value["Fn::GetAtt"][0];
    }
    return undefined;
}

/** Every logical id a resource refers to through `Ref` or `Fn::GetAtt`, anywhere in its body. */
function referencedIds(value: any, into: Set<string> = new Set()): Set<string> {
    if (Array.isArray(value)) {
        value.forEach((v) => referencedIds(v, into));
    } else if (value && typeof value === "object") {
        const id = referencedId(value);
        if (id) into.add(id);
        Object.values(value).forEach((v) => referencedIds(v, into));
    }
    return into;
}

/** One emitted template, indexed for dependency questions. */
class StackGraph {
    private readonly byId = new Map<string, Resource>();
    private readonly ancestorCache = new Map<string, Set<string>>();

    constructor(readonly stack: string, resources: Resource[]) {
        for (const r of resources) this.byId.set(r.logicalId, r);
    }

    get(id: string | undefined): Resource | undefined {
        return id === undefined ? undefined : this.byId.get(id);
    }

    all(): Resource[] {
        return Array.from(this.byId.values());
    }

    /** Direct predecessors: `DependsOn` plus every resource referenced by `Ref` / `Fn::GetAtt`. */
    private direct(id: string): string[] {
        const r = this.byId.get(id);
        if (!r) return [];
        const deps = new Set<string>(asArray<string>(r.raw.DependsOn));
        referencedIds(r.raw.Properties ?? {}, deps);
        return Array.from(deps).filter((d) => d !== id && this.byId.has(d));
    }

    /** Every resource CloudFormation must finish before it starts this one. */
    ancestors(id: string): Set<string> {
        const cached = this.ancestorCache.get(id);
        if (cached) return cached;
        const seen = new Set<string>();
        const stack = [...this.direct(id)];
        for (let next = stack.pop(); next !== undefined; next = stack.pop()) {
            if (seen.has(next)) continue;
            seen.add(next);
            stack.push(...this.direct(next));
        }
        this.ancestorCache.set(id, seen);
        return seen;
    }

    /** The role a Lambda function in this template runs as. */
    functionRole(functionId: string | undefined): string | undefined {
        const fn = this.get(functionId);
        return fn?.type === "AWS::Lambda::Function" ? referencedId(fn.properties.Role) : undefined;
    }

    /**
     * The roles a custom resource's code runs as: the provider function's, and for a `cr.Provider`
     * also the user handlers' that the framework function invokes.
     */
    rolesActingFor(customResource: Resource): string[] {
        const providerId = referencedId(customResource.properties.ServiceToken);
        const roles = [this.functionRole(providerId)];
        const env = this.get(providerId)?.properties.Environment?.Variables ?? {};
        for (const key of ["USER_ON_EVENT_FUNCTION_ARN", "USER_IS_COMPLETE_FUNCTION_ARN"]) {
            roles.push(this.functionRole(referencedId(env[key])));
        }
        return Array.from(new Set(roles.filter((r): r is string => r !== undefined)));
    }

    /** Standalone IAM policies attached to the role that allow any `kms:` action. */
    kmsPoliciesOn(roleId: string): Resource[] {
        return this.all().filter(
            (p) =>
                (p.type === "AWS::IAM::Policy" || p.type === "AWS::IAM::ManagedPolicy") &&
                asArray<any>(p.properties.Roles).some((r) => referencedId(r) === roleId) &&
                asArray<any>(p.properties.PolicyDocument?.Statement).some(
                    (s) =>
                        s.Effect === "Allow" &&
                        asArray<string>(s.Action).some((a) => a.startsWith("kms:"))
                )
        );
    }
}

/** The KMS grants of a policy as `action|resource|condition` strings, for comparing two policies. */
function kmsGrants(policy: Resource): string[] {
    return asArray<any>(policy.properties.PolicyDocument?.Statement)
        .filter((s) => s.Effect === "Allow")
        .flatMap((s) =>
            asArray<string>(s.Action)
                .filter((a) => a.startsWith("kms:"))
                .flatMap((a) =>
                    asArray<any>(s.Resource).map(
                        (r) => `${a}|${JSON.stringify(r)}|${JSON.stringify(s.Condition ?? {})}`
                    )
                )
        );
}

function graphs(synth: SynthResult): StackGraph[] {
    const byStack = new Map<string, Resource[]>();
    for (const r of synth.resources) {
        byStack.set(r.stack, [...(byStack.get(r.stack) ?? []), r]);
    }
    return Array.from(byStack.entries()).map(([stack, rs]) => new StackGraph(stack, rs));
}

/** The template holding a role whose logical id starts with `rolePrefix`, and that role's id. */
function findRole(synth: SynthResult, rolePrefix: string): { graph: StackGraph; roleId: string } {
    const hits = graphs(synth).flatMap((graph) =>
        graph
            .all()
            .filter((r) => r.type === "AWS::IAM::Role" && r.logicalId.startsWith(rolePrefix))
            .map((r) => ({ graph, roleId: r.logicalId }))
    );
    expect(hits.map((h) => h.roleId)).toHaveLength(1);
    return hits[0];
}

/** Custom resources whose code runs as the role. */
function customResourcesRunningAs(graph: StackGraph, roleId: string): Resource[] {
    return graph
        .all()
        .filter((r) => CUSTOM_RESOURCE(r.type) && graph.rolesActingFor(r).includes(roleId));
}

/** Seed roles whose KMS grant is a separately attached policy, with the seeds known to use them. */
const SEED_ROLES: { name: string; rolePrefix: string; minSeeds: number }[] = [
    // System and admin login profiles, the admin and read-only roles, their user-role links and the
    // denormalized constraint rows.
    { name: "auth defaults", rolePrefix: "AuthDefaultCustomResourceRole", minSeeds: 10 },
    // The four GLOBAL default metadata schemas the commercial template auto-loads.
    {
        name: "metadata schema defaults",
        rolePrefix: "MetadataSchemaDefaultCustomResourceRole",
        minSeeds: 4,
    },
];

describe("custom resources wait for the KMS grant of the role they run as", () => {
    describe.each(SEED_ROLES)("$name seeds", ({ rolePrefix, minSeeds }) => {
        it("run as the seed role in both syntheses, so neither direction is vacuous", () => {
            for (const synth of [withCmk(), withoutCmk()]) {
                const { graph, roleId } = findRole(synth, rolePrefix);
                expect(customResourcesRunningAs(graph, roleId).length).toBeGreaterThanOrEqual(
                    minSeeds
                );
            }
        });

        it("get the key from one policy attached to the role, scoped to a key, when the CMK is on", () => {
            const { graph, roleId } = findRole(withCmk(), rolePrefix);
            const policies = graph.kmsPoliciesOn(roleId);
            expect(policies.map((p) => p.logicalId)).toEqual([
                expect.stringMatching(/^CRAuthKmsPolicy/),
            ]);
            for (const statement of asArray<any>(policies[0].properties.PolicyDocument.Statement)) {
                expect(statement.Resource).not.toEqual("*");
            }
        });

        it("each carry DependsOn on that policy when the CMK is on", () => {
            const { graph, roleId } = findRole(withCmk(), rolePrefix);
            const [policy] = graph.kmsPoliciesOn(roleId);
            const missing = customResourcesRunningAs(graph, roleId)
                .filter((r) => !asArray<string>(r.raw.DependsOn).includes(policy.logicalId))
                .map((r) => r.logicalId);
            expect(missing).toEqual([]);
        });

        it("have no KMS policy to wait for when the CMK is off", () => {
            const { graph, roleId } = findRole(withoutCmk(), rolePrefix);
            expect(graph.kmsPoliciesOn(roleId)).toEqual([]);
            expect(
                withoutCmk().where("AWS::IAM::Policy", (p) => /^CRAuthKmsPolicy/.test(p.logicalId))
            ).toEqual([]);
        });
    });

    describe("every custom resource in the app", () => {
        /** (custom resource, KMS policy on a role its code runs as) pairs across every template. */
        function pairs(synth: SynthResult) {
            return graphs(synth).flatMap((graph) =>
                graph
                    .all()
                    .filter((r) => CUSTOM_RESOURCE(r.type))
                    .flatMap((cr) =>
                        graph.rolesActingFor(cr).flatMap((roleId) =>
                            graph.kmsPoliciesOn(roleId).map((policy) => ({
                                graph,
                                customResource: cr.logicalId,
                                roleId,
                                policy,
                                ordered: graph.ancestors(cr.logicalId).has(policy.logicalId),
                            }))
                        )
                    )
            );
        }

        /**
         * Pairs whose KMS grant is not in place when the custom resource runs: the policy is not an
         * ancestor, and no ancestor policy on the same role grants the same thing. The second clause
         * admits `AwsCustomResource`s that share one singleton provider role: each one depends on its
         * own policy, which carries the same key statement as its siblings' policies.
         */
        function unordered(synth: SynthResult): string[] {
            const all = pairs(synth);
            return all
                .filter((p) => !p.ordered)
                .filter((p) => {
                    const granted = new Set(
                        all
                            .filter(
                                (q) =>
                                    q.ordered &&
                                    q.graph === p.graph &&
                                    q.customResource === p.customResource &&
                                    q.roleId === p.roleId
                            )
                            .flatMap((q) => kmsGrants(q.policy))
                    );
                    return kmsGrants(p.policy).some((g) => !granted.has(g));
                })
                .map((p) => `${p.graph.stack}: ${p.customResource} -> ${p.policy.logicalId}`);
        }

        it("the check covers custom resources whose roles hold a KMS policy", () => {
            // Control for the walk itself: provider roles, user-handler roles, and policies attached
            // both ways must all be found, or the assertions below pass against nothing.
            const found = pairs(withCmk());
            expect(found.length).toBeGreaterThan(20);
            expect(found.some((p) => /^CRAuthKmsPolicy/.test(p.policy.logicalId))).toBe(true);
            expect(found.some((p) => /DefaultPolicy/.test(p.policy.logicalId))).toBe(true);
            expect(found.some((p) => /CustomResourcePolicy/.test(p.policy.logicalId))).toBe(true);
        });

        it("every KMS grant is in place before the custom resource runs when the CMK is on", () => {
            expect(unordered(withCmk())).toEqual([]);
        });

        it("every KMS grant is in place before the custom resource runs when the CMK is off", () => {
            expect(unordered(withoutCmk())).toEqual([]);
        });
    });
});
