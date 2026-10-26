/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The EDH Workstations add-on's capacity reservation surface in the stack.
 *
 * VAMS launches a desktop into an EC2 capacity reservation an administrator registered, and reads
 * every registered reservation live from Amazon EC2 rather than storing its facts. What is pinned
 * here:
 *   * the user catalog route is registered on the workstation service, and the three admin paths
 *     (list and register, update and unregister) on the admin service, behind the same token-keyed
 *     authorizer as the lease settings route; a stock template registers none of them;
 *   * `ec2:DescribeCapacityReservations` is granted on `*` (EC2 offers no resource-level scope for a
 *     Describe call) to exactly the workstation service, the launch worker and the admin service,
 *     and to no other role of the deployment; with the add-on off no role holds it;
 *   * no add-on role may create, modify, cancel or move a capacity reservation; the one EC2 write
 *     any add-on role holds is the launch worker's move of a stopped desktop off a reservation that
 *     has ended (`ec2:ModifyInstanceCapacityReservationAttributes`), on this account's and Region's
 *     instances tagged with EDH's cluster id, and the three roles' own policies hold no other EC2
 *     action than their reads (the managed VPC-access policy every in-VPC Lambda carries is not one
 *     of them);
 *   * the workstation service and the admin service read EDH's private subnets parameter, by its
 *     exact name, and `ec2:DescribeSubnets` on `*`; with the add-on off no role holds either.
 *
 * The CDK Nag suppressions are held by `edhNag.test.ts`.
 */

import {
    Resource,
    SynthResult,
    TemplateName,
    actionsOf,
    expectAbsent,
    handlerOf,
    resolveReference,
    resourcesOf,
    restApiPairs,
    statementsOf,
    synthTemplate,
} from "../../support/templateSynth";
import {
    edhFunctions,
    enableEdh,
    enableEdhAmiBuilds,
    enableEdhContainers,
    inEdhStack,
} from "./support/edhSynth";

// A full-app synth is ~20 s and this file needs four: both EDH templates with the add-on on, the
// commercial template with every optional add-on feature on, and the stock commercial template.
jest.setTimeout(600_000);

const EDH_TEMPLATES: TemplateName[] = ["commercial", "govcloud"];

const DESCRIBE_CAPACITY_RESERVATIONS = "ec2:DescribeCapacityReservations";

/** The three roles that read a registered reservation: the launch checks, the worker retry, the registry. */
const RESERVATION_READERS = ["edhAdminService", "edhLaunchWorker", "edhWorkstationService"];

/** Each capacity reservation method+path pair, with the add-on module that backs it. */
const CAPACITY_RESERVATION_ROUTES: Array<[string, string]> = [
    ["GET /addon/edh/catalog/capacity-reservations", "edhWorkstationService"],
    ["GET /addon/edh/admin/capacity-reservations", "edhAdminService"],
    ["POST /addon/edh/admin/capacity-reservations", "edhAdminService"],
    ["PUT /addon/edh/admin/capacity-reservations/{capacityReservationId}", "edhAdminService"],
    ["DELETE /addon/edh/admin/capacity-reservations/{capacityReservationId}", "edhAdminService"],
];

/**
 * EC2 actions that change a capacity reservation, or what an instance is placed in. None may be
 * granted to an add-on role, by name or through a wildcard; the launch worker's one EC2 write, which
 * moves a stopped desktop off an ended reservation, is pinned on its own below.
 */
const CAPACITY_WRITES = [
    "ec2:CreateCapacityReservation",
    "ec2:CreateCapacityReservationBySplitting",
    "ec2:CreateCapacityReservationFleet",
    "ec2:ModifyCapacityReservation",
    "ec2:ModifyCapacityReservationFleet",
    "ec2:CancelCapacityReservation",
    "ec2:CancelCapacityReservationFleets",
    "ec2:MoveCapacityReservationInstances",
    "ec2:AssociateCapacityReservationBillingOwner",
    "ec2:RunInstances",
    "ec2:StartInstances",
];

/** The launch worker's move of a stopped desktop off an ended reservation, onto on-demand capacity. */
const MOVE_TO_ON_DEMAND = "ec2:ModifyInstanceCapacityReservationAttributes";
/** The cluster id the EDH synth configures (`support/edhSynth.ts`), which EDH tags every desktop with. */
const EDH_CLUSTER_ID = "edh-t1";
/** The instances of the deployment's account and Region, as the template flattens the ARN. */
const INSTANCE_ARN = /^arn:\$\{AWS::Partition\}:ec2:[a-z0-9-]+:123456789012:instance\/\*$/;
/** The two roles that read which Availability Zones EDH has a private subnet in. */
const SUBNET_READERS = ["edhAdminService", "edhWorkstationService"];
const DESCRIBE_SUBNETS = "ec2:DescribeSubnets";
/** EDH's private subnets parameter, exactly, as the template flattens its ARN. */
const PRIVATE_SUBNETS_PARAMETER_ARN = new RegExp(
    `^arn:\\$\\{AWS::Partition\\}:ssm:[a-z0-9-]+:123456789012:parameter/edh/${EDH_CLUSTER_ID}/configuration/PrivateSubnets$`
);

const edhSynth = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enableEdh, mutateKey: "edhWorkstationsEnabled" });

/** The add-on with containers and AMI builds on: every add-on Lambda the stack can emit. */
const allFeaturesSynth = (): SynthResult =>
    synthTemplate("commercial", {
        mutate: (c: any) => {
            // The containers hybrid re-applies the add-on base, which leaves the AMI builds block
            // alone, so it comes second.
            enableEdhAmiBuilds(c);
            enableEdhContainers(c);
        },
        mutateKey: "edhAmiBuildsAndContainers",
    });

/** `handlers.addon.edh.<module>.lambda_handler` -> `<module>`. */
const moduleOf = (r: Resource): string => handlerOf(r).split(".")[3];

/** The REST API and the operation of one `METHOD /path` pair; `op` is undefined for an unregistered pair. */
function operationOf(s: SynthResult, pair: string): { api: Resource; op: any } {
    const [method, routePath] = pair.split(" ");
    const apis = s.ofType("AWS::ApiGateway::RestApi");
    expect(apis).toHaveLength(1);
    return { api: apis[0], op: apis[0].properties.Body.paths[routePath]?.[method.toLowerCase()] };
}

/** Every `Ref` and `Fn::GetAtt` object inside a CloudFormation value. */
function referencesIn(value: unknown): unknown[] {
    if (Array.isArray(value)) return value.flatMap(referencesIn);
    if (value !== null && typeof value === "object") {
        const o = value as Record<string, unknown>;
        if ("Ref" in o || "Fn::GetAtt" in o) return [o];
        return Object.values(o).flatMap(referencesIn);
    }
    return [];
}

/** The add-on module a route's Lambda proxy integration invokes, resolved across nested stacks. */
function backingModule(s: SynthResult, pair: string): string {
    const { api, op } = operationOf(s, pair);
    if (!op) return "<no operation>";
    const functions = referencesIn(op["x-amazon-apigateway-integration"]?.uri)
        .map((ref) => resolveReference(s, api.stack, ref))
        .filter((r): r is Resource => r?.type === "AWS::Lambda::Function");
    return functions.length === 1 ? moduleOf(functions[0]) : `<${functions.length} functions>`;
}

/** Allowed IAM statements on a function's role. */
const allowed = (s: SynthResult, fn: Resource): any[] =>
    statementsOf(s, fn).filter((st) => st.Effect === "Allow");

/** Whether an IAM action pattern covers an action (IAM matches case-insensitively, `*` and `?`). */
const covers = (pattern: string, action: string): boolean =>
    new RegExp(`^${pattern.replace(/\*/g, ".*").replace(/\?/g, ".")}$`, "i").test(action);

/** The add-on modules whose role is allowed `ec2:DescribeCapacityReservations`. */
const describeHolders = (s: SynthResult): string[] =>
    edhFunctions(s)
        .filter((fn) =>
            allowed(s, fn).some((st) => actionsOf(st).includes(DESCRIBE_CAPACITY_RESERVATIONS))
        )
        .map(moduleOf)
        .sort();

/** Every IAM statement of the deployment that names the action, inline and managed alike. */
const describeStatementsAnywhere = (s: SynthResult): Array<{ stack: string; logicalId: string }> =>
    [...s.ofType("AWS::IAM::Policy"), ...s.ofType("AWS::IAM::ManagedPolicy")].flatMap((p) =>
        ((p.properties.PolicyDocument?.Statement ?? []) as any[])
            .filter((st) => actionsOf(st).includes(DESCRIBE_CAPACITY_RESERVATIONS))
            .map(() => ({ stack: p.stack, logicalId: p.logicalId }))
    );

describe("EDH capacity reservation routes", () => {
    test.each(EDH_TEMPLATES)("%s: the hybrid registers every capacity reservation pair", (name) => {
        const pairs = restApiPairs(edhSynth(name));
        expect(
            CAPACITY_RESERVATION_ROUTES.map(([pair]) => pair).filter((p) => !pairs.has(p))
        ).toEqual([]);
    });

    test.each(EDH_TEMPLATES)(
        "%s: the catalog route is served by the workstation service and the admin paths by the admin service",
        (name) => {
            const s = edhSynth(name);
            // Control: the resolver reads the module behind a route the stack has always wired.
            expect(backingModule(s, "GET /addon/edh/admin/settings")).toBe("edhAdminService");
            expect(backingModule(s, "GET /addon/edh/catalog/lease")).toBe("edhWorkstationService");
            expect(
                CAPACITY_RESERVATION_ROUTES.map(([pair]) => [pair, backingModule(s, pair)])
            ).toEqual(CAPACITY_RESERVATION_ROUTES);
        }
    );

    test.each(EDH_TEMPLATES)(
        "%s: every capacity reservation operation sits behind the authorizer the lease settings route uses",
        (name) => {
            const s = edhSynth(name);
            const settings = operationOf(s, "GET /addon/edh/admin/settings").op;
            expect(settings.security).toEqual([{ VamsAuthorizer: [] }]);
            for (const [pair] of CAPACITY_RESERVATION_ROUTES) {
                expect([pair, operationOf(s, pair).op?.security]).toEqual([
                    pair,
                    settings.security,
                ]);
            }
        }
    );

    test("the stock template registers no capacity reservation route", () => {
        const stock = restApiPairs(synthTemplate("commercial"));
        expectAbsent(
            "capacity reservation routes in the stock commercial template",
            [...stock].filter((pair) => pair.includes("/capacity-reservations")),
            {
                description: "the commercial hybrid registers the capacity reservation routes",
                count: [...restApiPairs(edhSynth("commercial"))].filter((pair) =>
                    pair.includes("/capacity-reservations")
                ).length,
            }
        );
    });
});

describe("EDH capacity reservation IAM", () => {
    test.each(EDH_TEMPLATES)(
        "%s: the workstation service, the launch worker and the admin service read reservations on every resource",
        (name) => {
            const s = edhSynth(name);
            expect(describeHolders(s)).toEqual(RESERVATION_READERS);
            for (const fn of edhFunctions(s).filter((f) =>
                RESERVATION_READERS.includes(moduleOf(f))
            )) {
                const statements = allowed(s, fn).filter((st) =>
                    actionsOf(st).includes(DESCRIBE_CAPACITY_RESERVATIONS)
                );
                // EC2 has no resource-level scope for a Describe call, and no condition narrows it.
                expect([moduleOf(fn), [...new Set(statements.flatMap(resourcesOf))]]).toEqual([
                    moduleOf(fn),
                    ["*"],
                ]);
                expect(statements.filter((st) => st.Condition !== undefined)).toEqual([]);
            }
        }
    );

    test("with every add-on feature on, the grant stays on exactly the three roles", () => {
        const s = allFeaturesSynth();
        // Control: the feature set emits the container and AMI recipe Lambdas as well.
        expect(edhFunctions(s).map(moduleOf)).toEqual(
            expect.arrayContaining(["edhContainerService", "edhAmiRecipeService"])
        );
        expect(describeHolders(s)).toEqual(RESERVATION_READERS);
        // No role outside the three, in any stack of the deployment, names the action.
        const readerRoles = edhFunctions(s)
            .filter((fn) => RESERVATION_READERS.includes(moduleOf(fn)))
            .map((fn) => SynthResult.flatten(fn.properties.Role).replace(/^\$\{|\.Arn\}$/g, ""));
        const outside = [...s.ofType("AWS::IAM::Policy"), ...s.ofType("AWS::IAM::ManagedPolicy")]
            .filter((p) =>
                ((p.properties.PolicyDocument?.Statement ?? []) as any[]).some((st) =>
                    actionsOf(st).includes(DESCRIBE_CAPACITY_RESERVATIONS)
                )
            )
            .filter(
                (p) =>
                    !inEdhStack(p) ||
                    !SynthResult.flatten(p.properties.Roles)
                        .split(/\$\{|\}/)
                        .some((id) => readerRoles.includes(id))
            )
            .map((p) => `${p.stack}/${p.logicalId}`);
        expect(outside).toEqual([]);
    });

    test("no add-on role may change a capacity reservation or launch an instance", () => {
        const s = allFeaturesSynth();
        const fns = edhFunctions(s);
        // Control: the matcher finds an EC2 write the AMI build Lambdas hold on their own AMIs.
        expect(
            fns.some((fn) =>
                allowed(s, fn)
                    .flatMap(actionsOf)
                    .some((a) => covers(a, "ec2:DeregisterImage"))
            )
        ).toBe(true);
        const writes = fns.flatMap((fn) =>
            allowed(s, fn)
                .flatMap(actionsOf)
                .filter((a) => CAPACITY_WRITES.some((w) => covers(a, w)))
                .map((a) => `${moduleOf(fn)}: ${a}`)
        );
        expect(writes).toEqual([]);
    });

    test("only the launch worker may move an instance off a reservation, and only an EDH desktop of this cluster", () => {
        const s = allFeaturesSynth();
        const holders = edhFunctions(s)
            .filter((fn) =>
                allowed(s, fn)
                    .flatMap(actionsOf)
                    .some((a) => covers(a, MOVE_TO_ON_DEMAND))
            )
            .map(moduleOf);
        expect(holders).toEqual(["edhLaunchWorker"]);
        const [worker] = edhFunctions(s).filter((fn) => moduleOf(fn) === "edhLaunchWorker");
        const statements = allowed(s, worker).filter((st) =>
            actionsOf(st).some((a) => covers(a, MOVE_TO_ON_DEMAND))
        );
        expect(statements).toHaveLength(1);
        const [move] = statements;
        // The one action, by name, on the instances of the deployment's account and Region only.
        expect(actionsOf(move)).toEqual([MOVE_TO_ON_DEMAND]);
        const resources = resourcesOf(move);
        expect(resources).toHaveLength(1);
        expect(resources[0]).toMatch(INSTANCE_ARN);
        // Conditioned on the two tags EDH puts on every desktop: the configured cluster id, and the
        // desktop node type, which EDH's controller, login node, broker and gateway do not carry.
        expect(move.Condition).toEqual({
            StringEquals: {
                "aws:ResourceTag/edh:ClusterId": EDH_CLUSTER_ID,
                "aws:ResourceTag/edh:NodeType": "dcv_node",
            },
        });
    });

    test.each(EDH_TEMPLATES)(
        "%s: the workstation service and the admin service read EDH's private subnets parameter, and only it",
        (name) => {
            const s = edhSynth(name);
            const parameterReads = Object.fromEntries(
                edhFunctions(s)
                    .map((fn) => [
                        moduleOf(fn),
                        allowed(s, fn)
                            .filter((st) => actionsOf(st).includes("ssm:GetParameter"))
                            .flatMap(resourcesOf)
                            .filter((r) => r.includes(":parameter/edh/")),
                    ])
                    .filter(([, reads]) => reads.length > 0)
            );
            expect(Object.keys(parameterReads).sort()).toEqual(SUBNET_READERS);
            for (const module of SUBNET_READERS) {
                expect(parameterReads[module]).toHaveLength(1);
                expect(parameterReads[module][0]).toMatch(PRIVATE_SUBNETS_PARAMETER_ARN);
            }
            const subnetReaders = edhFunctions(s)
                .filter((fn) =>
                    allowed(s, fn).some((st) => actionsOf(st).includes(DESCRIBE_SUBNETS))
                )
                .map(moduleOf)
                .sort();
            expect(subnetReaders).toEqual(SUBNET_READERS);
            for (const fn of edhFunctions(s).filter((f) => SUBNET_READERS.includes(moduleOf(f)))) {
                const describes = allowed(s, fn).filter((st) =>
                    actionsOf(st).includes(DESCRIBE_SUBNETS)
                );
                // EC2 has no resource-level scope for a Describe call.
                expect([moduleOf(fn), [...new Set(describes.flatMap(resourcesOf))]]).toEqual([
                    moduleOf(fn),
                    ["*"],
                ]);
            }
        }
    );

    test("with the add-on off, no role of the deployment holds the move or the subnet reads", () => {
        const stock = synthTemplate("commercial");
        const named = (s: SynthResult, match: (st: any) => boolean): string[] =>
            [...s.ofType("AWS::IAM::Policy"), ...s.ofType("AWS::IAM::ManagedPolicy")].flatMap((p) =>
                ((p.properties.PolicyDocument?.Statement ?? []) as any[])
                    .filter(match)
                    .map(() => `${p.stack}/${p.logicalId}`)
            );
        const addonStatement = (st: any): boolean =>
            actionsOf(st).includes(MOVE_TO_ON_DEMAND) ||
            resourcesOf(st).some((r) => r.includes(":parameter/edh/"));
        expectAbsent(
            "the add-on's instance move and EDH parameter read in the stock template",
            named(stock, addonStatement),
            {
                description: "the commercial hybrid grants them",
                count: named(edhSynth("commercial"), addonStatement).length,
            }
        );
    });

    test("the three reader roles' own policies hold no EC2 action but the reservation and subnet reads, and the worker's instance read and move", () => {
        const s = allFeaturesSynth();
        const ec2Actions = Object.fromEntries(
            edhFunctions(s)
                .filter((fn) => RESERVATION_READERS.includes(moduleOf(fn)))
                .map((fn) => [
                    moduleOf(fn),
                    [
                        ...new Set(
                            allowed(s, fn)
                                .flatMap(actionsOf)
                                .filter((a) => /^ec2:/i.test(a))
                        ),
                    ],
                ])
        );
        // The launch worker also reads a stopped desktop's instance, for the Availability Zone a
        // refused start names, and moves one off an ended reservation; the workstation and admin
        // services also read the zones of EDH's private subnets.
        expect(ec2Actions).toEqual(
            Object.fromEntries(
                RESERVATION_READERS.map((module) => [
                    module,
                    module === "edhLaunchWorker"
                        ? [
                              DESCRIBE_CAPACITY_RESERVATIONS,
                              "ec2:DescribeInstances",
                              MOVE_TO_ON_DEMAND,
                          ]
                        : [DESCRIBE_CAPACITY_RESERVATIONS, DESCRIBE_SUBNETS],
                ])
            )
        );
    });

    test("with the add-on off, no role of the deployment reads capacity reservations", () => {
        const stock = synthTemplate("commercial");
        expectAbsent(
            "ec2:DescribeCapacityReservations statements in the stock commercial template",
            describeStatementsAnywhere(stock),
            {
                description: "the commercial hybrid grants ec2:DescribeCapacityReservations",
                count: describeStatementsAnywhere(edhSynth("commercial")).length,
            }
        );
    });
});
