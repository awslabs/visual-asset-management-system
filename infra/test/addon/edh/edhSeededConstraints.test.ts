/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The seeded `admin` role carries one allow-all Tier-2 constraint per Casbin object type, and the
 * seeded `basicReadOnly` role reaches the EDH workstation page, the read routes, and GET on every
 * workstation and catalog image, the EDH object types only while the add-on is enabled.
 *
 * The object types are read from the backend's Python source, so this suite needs no interpreter:
 * the core types from `CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES` in `common/constants.py`, and the
 * add-on types from the keys of `ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS` in `common/addonConstants.py`.
 * The core seeding of the two auth-default constructs covers the core types, and
 * `addonDefaultConstraints.ts` covers the EDH types while the add-on is on; with it off, neither
 * role carries an EDH object type, constraint or route value.
 *
 * Both constructs write literal `putItem` payloads, so the emitted `Custom::AWS` resources are read
 * back here. Each construct is instantiated directly under a top-level stack with narrow stand-ins
 * for the three table names and the config leaves it reads, which keeps this file off the full-app
 * synth path. Every negative assertion is paired with a control that reads a value the construct
 * has always carried, so a parse that read nothing cannot pass.
 */

import * as cdk from "aws-cdk-lib";
import * as fs from "fs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import {
    addonAdminDefaultConstraints,
    addonReadOnlyDefaultConstraints,
} from "../../../lib/nestedStacks/addon/addonDefaultConstraints";
import { DynamoDbAuthDefaultsAdminConstructStack } from "../../../lib/nestedStacks/auth/constructs/dynamodb-authdefaults-admin-construct";
import { DynamoDbAuthDefaultsROConstructStack } from "../../../lib/nestedStacks/auth/constructs/dynamodb-authdefaults-ro-construct";
import { newTestApp } from "../../support/testApp";

const BACKEND_COMMON = path.resolve(
    __dirname,
    "..",
    "..",
    "..",
    "..",
    "backend",
    "backend",
    "common"
);
const CONSTANTS_PY = path.join(BACKEND_COMMON, "constants.py");
const ADDON_CONSTANTS_PY = path.join(BACKEND_COMMON, "addonConstants.py");

/** The constraint ids the add-on appends to the admin role, in order. */
const EDH_ADMIN_CONSTRAINT_IDS = [
    "initial_admin_allow_all_edhworkstations#group#admin",
    "initial_admin_allow_all_edhimages#group#admin",
    "initial_admin_allow_all_edhcontainers#group#admin",
    "initial_admin_allow_all_edhcapacityreservations#group#admin",
];

/** The constraint ids the add-on appends to the read-only role, in order. */
const EDH_READ_ONLY_CONSTRAINT_IDS = [
    "initial_basicro_allow_get_edhworkstations#group#basicReadOnly",
    "initial_basicro_allow_get_edhimages#group#basicReadOnly",
];

/** The backend's `CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES`, read from the Python source. */
function coreObjectTypes(): string[] {
    const source = fs.readFileSync(CONSTANTS_PY, "utf8");
    const block = source.match(/^CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES\s*=\s*\[([\s\S]*?)\]/m);
    if (!block) {
        throw new Error(`CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES not found in ${CONSTANTS_PY}`);
    }
    return [...block[1].matchAll(/'([A-Za-z]+)'/g)].map((m) => m[1]);
}

/** The keys of the backend's `ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS`, read from the Python source. */
function addonObjectTypes(): string[] {
    const source = fs.readFileSync(ADDON_CONSTANTS_PY, "utf8");
    const start = source.search(/^ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS\b[^=\n]*=\s*\{/m);
    if (start < 0) {
        throw new Error(`ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS not found in ${ADDON_CONSTANTS_PY}`);
    }
    // The dict closes on a line of its own; its keys are the entries at four spaces of indent.
    const body = source.slice(start).split(/^\}/m)[0];
    return [...body.matchAll(/^ {4}["']([A-Za-z]+)["']\s*:/gm)].map((m) => m[1]);
}

/** The add-on object types the EDH Workstations add-on declares. */
const edhObjectTypes = (): string[] => addonObjectTypes().filter((t) => /^edh[A-Z]/.test(t));

const STORAGE = {
    dynamo: {
        rolesStorageTable: { tableName: "wp02-roles" },
        userRolesStorageTable: { tableName: "wp02-user-roles" },
        constraintsStorageTable: { tableName: "wp02-constraints" },
    },
} as any;

/** The config leaves the constructs and `addonDefaultConstraints.ts` read. */
const seedConfig = (edhEnabled: boolean): any => ({
    app: {
        adminUserId: "wp02-admin@example.com",
        authProvider: { useCognito: { enabled: false, useSaml: false, useOidc: false } },
        addons: {
            usePhysnaSync: { enabled: false },
            useEdhWorkstations: { enabled: edhEnabled },
        },
    },
});

function customResourceRole(stack: cdk.Stack): iam.Role {
    return new iam.Role(stack, "CustomResourceRole", {
        assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });
}

/** The parsed SDK-call payloads of every `Custom::AWS` resource in the template. */
function putItemPayloads(template: Template): any[] {
    return Object.values(template.findResources("Custom::AWS")).map((r: any) => {
        const text = r.Properties.Create ?? r.Properties.Update;
        if (typeof text !== "string") {
            throw new Error(
                `expected a literal SDK-call payload, got ${JSON.stringify(text).slice(0, 200)}`
            );
        }
        return JSON.parse(text);
    });
}

/**
 * Constraint rows only. The role and user-role seeds share the resource type but carry
 * `roleName` rather than `constraintId`.
 */
function constraintItems(payloads: any[]): any[] {
    return payloads
        .map((p) => p.parameters?.Item)
        .filter((item) => item !== undefined && "constraintId" in item);
}

/** Every criterion of a denormalized constraint row (both lists are stored as JSON strings). */
function criteriaOf(item: any): any[] {
    return [...JSON.parse(item.criteriaOr.S), ...JSON.parse(item.criteriaAnd.S)];
}

/** Constraint rows in seeding order, read from the index each custom resource's id carries. */
function indexedConstraints(payloads: any[], role: string): string[] {
    return payloads
        .filter((p) => "constraintId" in (p.parameters?.Item ?? {}))
        .map((p) => {
            const id = String(p.physicalResourceId?.id ?? "");
            const m = id.match(new RegExp(`_initialization${role}_(\\d+)$`));
            if (!m) throw new Error(`unexpected physicalResourceId ${JSON.stringify(id)}`);
            return { index: Number(m[1]), constraintId: p.parameters.Item.constraintId.S };
        })
        .sort((a, b) => a.index - b.index)
        .map((c) => c.constraintId);
}

const isEdhConstraint = (item: any): boolean =>
    /^edh/.test(item.objectType.S) || /edh/i.test(item.constraintId.S);

function synthAdmin(edhEnabled: boolean): Template {
    const app = newTestApp();
    const stack = new cdk.Stack(app, `Wp02AdminSeedStack${edhEnabled ? "Edh" : "Base"}`);
    new DynamoDbAuthDefaultsAdminConstructStack(stack, "AuthDefaultsAdmin", {
        customResourceRole: customResourceRole(stack),
        lambdaCommonBaseLayer: undefined as any,
        storageResources: STORAGE,
        config: seedConfig(edhEnabled),
    });
    return Template.fromStack(stack);
}

function synthReadOnly(edhEnabled: boolean): Template {
    const app = newTestApp();
    const stack = new cdk.Stack(app, `Wp02ReadOnlySeedStack${edhEnabled ? "Edh" : "Base"}`);
    new DynamoDbAuthDefaultsROConstructStack(stack, "AuthDefaultsRO", {
        customResourceRole: customResourceRole(stack),
        lambdaCommonBaseLayer: undefined as any,
        storageResources: STORAGE,
        config: seedConfig(edhEnabled),
    });
    return Template.fromStack(stack);
}

describe("the backend object type lists", () => {
    test("both Python lists parse (control)", () => {
        expect(coreObjectTypes()).toEqual(expect.arrayContaining(["asset", "api", "web"]));
        expect(edhObjectTypes().sort()).toEqual([
            "edhCapacityReservation",
            "edhContainer",
            "edhImage",
            "edhWorkstation",
        ]);
    });

    test("the core and add-on object types are disjoint", () => {
        const core = new Set(coreObjectTypes());
        expect(addonObjectTypes().filter((t) => core.has(t))).toEqual([]);
    });
});

describe("addonDefaultConstraints covers the EDH object types while the add-on is on", () => {
    test("admin: one constraint per EDH object type with the add-on on, none with it off", () => {
        const on = addonAdminDefaultConstraints(seedConfig(true), "admin");
        expect(on.map((item) => item.objectType.S).sort()).toEqual(edhObjectTypes().sort());
        expect(addonAdminDefaultConstraints(seedConfig(false), "admin")).toEqual([]);
    });

    test("read-only: GET on the workstation and image types with the add-on on, none with it off", () => {
        const on = addonReadOnlyDefaultConstraints(seedConfig(true), "basicReadOnly", "basicro");
        expect(on.map((item) => item.objectType.S).sort()).toEqual(["edhImage", "edhWorkstation"]);
        expect(
            addonReadOnlyDefaultConstraints(seedConfig(false), "basicReadOnly", "basicro")
        ).toEqual([]);
    });

    test("read-only: no capacity reservation is granted; an administrator grants each one", () => {
        // Control: the admin role is seeded the type, so the read-only list leaves it out by choice.
        expect(
            addonAdminDefaultConstraints(seedConfig(true), "admin").map((item) => item.objectType.S)
        ).toContain("edhCapacityReservation");
        expect(
            addonReadOnlyDefaultConstraints(seedConfig(true), "basicReadOnly", "basicro")
                .map((item) => item.objectType.S)
                .filter((type) => type === "edhCapacityReservation")
        ).toEqual([]);
    });

    test("a config without add-on settings seeds no add-on constraint", () => {
        const bare = { app: { adminUserId: "wp02-admin@example.com" } } as any;
        expect(addonAdminDefaultConstraints(bare, "admin")).toEqual([]);
        expect(addonReadOnlyDefaultConstraints(bare, "basicReadOnly", "basicro")).toEqual([]);
    });
});

describe("seeded admin Tier-2 constraints cover every Casbin object type", () => {
    const withEdhPayloads = putItemPayloads(synthAdmin(true));
    const withoutEdhPayloads = putItemPayloads(synthAdmin(false));
    const withEdh = constraintItems(withEdhPayloads);
    const withoutEdh = constraintItems(withoutEdhPayloads);
    const typesOf = (items: any[]): string[] => items.map((item) => item.objectType.S);

    test("the construct emits the seeded constraints (control)", () => {
        expect(withoutEdh.length).toBeGreaterThanOrEqual(11);
        expect(withEdh.length).toBe(withoutEdh.length + EDH_ADMIN_CONSTRAINT_IDS.length);
    });

    test("with the add-on off, the seeded objectType set equals the core types, each once", () => {
        expect(new Set(typesOf(withoutEdh))).toEqual(new Set(coreObjectTypes()));
        expect(typesOf(withoutEdh)).toHaveLength(new Set(typesOf(withoutEdh)).size);
    });

    test("with the add-on on, the seeded set is the core types plus the EDH types, each once", () => {
        expect(new Set(typesOf(withEdh))).toEqual(
            new Set([...coreObjectTypes(), ...edhObjectTypes()])
        );
        expect(typesOf(withEdh)).toHaveLength(new Set(typesOf(withEdh)).size);
    });

    test("with the add-on off, no EDH object type or constraint is seeded", () => {
        // Control: the same detector finds the EDH constraints with the add-on on.
        expect(withEdh.filter(isEdhConstraint)).toHaveLength(EDH_ADMIN_CONSTRAINT_IDS.length);
        expect(withoutEdh.filter(isEdhConstraint)).toEqual([]);
    });

    test("each EDH constraint is an allow-all on a field of its own type", () => {
        const byType = Object.fromEntries(withEdh.map((item) => [item.objectType.S, item]));
        const expected: Record<string, string> = {
            edhWorkstation: "edhImageId",
            edhImage: "edhImageId",
            edhContainer: "edhJobDefinition",
            edhCapacityReservation: "edhCapacityReservationId",
        };
        expect(Object.keys(expected).sort()).toEqual(edhObjectTypes().sort());
        for (const [type, field] of Object.entries(expected)) {
            expect(byType[type]).toBeDefined();
            expect(JSON.parse(byType[type].criteriaAnd.S)).toEqual([
                expect.objectContaining({ field, operator: "contains", value: ".*" }),
            ]);
            const permissions = JSON.parse(byType[type].groupPermissions.S);
            expect(permissions.map((p: any) => p.permission).sort()).toEqual([
                "DELETE",
                "GET",
                "POST",
                "PUT",
            ]);
            expect(
                permissions.every((p: any) => p.groupId === "admin" && p.permissionType === "allow")
            ).toBe(true);
        }
    });

    test("the EDH constraints take the indices after the core ones, which keep theirs", () => {
        // The loop derives each Custom::AWS logical id and physical id from its position, so every
        // core constraint has to keep the index it has without the add-on.
        const base = indexedConstraints(withoutEdhPayloads, "admin");
        const extended = indexedConstraints(withEdhPayloads, "admin");
        expect(base[0]).toBe("initial_admin_allow_all_web_paths#group#admin");
        expect(base[10]).toBe("initial_admin_allow_all_userroles#group#admin");
        expect(extended.slice(0, base.length)).toEqual(base);
        expect(extended.slice(base.length)).toEqual(EDH_ADMIN_CONSTRAINT_IDS);
    });
});

describe("seeded basicReadOnly EDH grants follow the add-on flag", () => {
    const withEdhPayloads = putItemPayloads(synthReadOnly(true));
    const withoutEdhPayloads = putItemPayloads(synthReadOnly(false));
    const withEdh = constraintItems(withEdhPayloads);
    const withoutEdh = constraintItems(withoutEdhPayloads);
    const edhTier2 = (items: any[]) => items.filter((item) => /^edh/.test(item.objectType.S));

    const WEB = "initial_basicro_allow_web_paths_get#group#basicReadOnly";
    const API_GET = "initial_basicro_allow_get_apis#group#basicReadOnly";

    /** route__path values of one seeded constraint, located by its denormalized id. */
    const routeValues = (items: any[], constraintId: string): string[] => {
        const found = items.filter((item) => item.constraintId.S === constraintId);
        expect(found).toHaveLength(1);
        return criteriaOf(found[0])
            .filter((c: any) => c.field === "route__path")
            .map((c: any) => String(c.value));
    };

    const allValues = (items: any[]): string[] =>
        items.flatMap((item) => criteriaOf(item).map((c: any) => String(c.value)));

    test("both syntheses seed the read-only constraints (control)", () => {
        expect(withoutEdh.length).toBeGreaterThanOrEqual(12);
        // The add-on adds the two Tier-2 constraints and widens two existing ones in place.
        expect(withEdh.length).toBe(withoutEdh.length + EDH_READ_ONLY_CONSTRAINT_IDS.length);
        expect(routeValues(withEdh, WEB)).toContain("/assets");
        expect(routeValues(withEdh, API_GET)).toContain("/auth/loginProfile");
    });

    test("with the add-on off, the Tier-2 object types are core types", () => {
        const core = new Set(coreObjectTypes());
        const tier2 = withoutEdh.map((item) => item.objectType.S);
        // Control: the read-only role seeds Tier-2 constraints on core types.
        expect(tier2.filter((t) => core.has(t)).length).toBeGreaterThan(0);
        expect(tier2.filter((t) => !core.has(t))).toEqual([]);
    });

    test("enabled: the workstations page and the two read route prefixes are granted", () => {
        expect(routeValues(withEdh, WEB)).toContain("/edh/workstations");
        expect(routeValues(withEdh, API_GET)).toEqual(
            expect.arrayContaining(["/addon/edh/workstations", "/addon/edh/catalog"])
        );
    });

    test("enabled: no admin prefix, and no verb other than GET is widened", () => {
        const values = allValues(withEdh);
        expect(
            values.filter((v) => v.startsWith("/addon/edh/admin") || v.startsWith("/edh/admin"))
        ).toEqual([]);
        for (const item of withEdh) {
            const permissions = JSON.parse(item.groupPermissions.S).map((p: any) => p.permission);
            if (permissions.every((p: string) => p === "GET")) continue;
            const edhValues = criteriaOf(item)
                .map((c: any) => String(c.value))
                .filter((v) => v.startsWith("/addon/edh") || v.startsWith("/edh"));
            expect(edhValues).toEqual([]);
        }
    });

    test("enabled: GET-only Tier-2 constraints on edhWorkstation and edhImage match every image", () => {
        const tier2 = edhTier2(withEdh);
        expect(tier2.map((item) => item.objectType.S).sort()).toEqual([
            "edhImage",
            "edhWorkstation",
        ]);
        expect(tier2.map((item) => item.constraintId.S).sort()).toEqual(
            [...EDH_READ_ONLY_CONSTRAINT_IDS].sort()
        );
        for (const item of tier2) {
            // The shape the edh-user permission template grants: one contains-anything criterion on
            // the image id; here with GET alone.
            expect(JSON.parse(item.criteriaAnd.S)).toEqual([
                expect.objectContaining({ field: "edhImageId", operator: "contains", value: ".*" }),
            ]);
            expect(JSON.parse(item.criteriaOr.S)).toEqual([]);
            expect(JSON.parse(item.groupPermissions.S)).toEqual([
                expect.objectContaining({
                    groupId: "basicReadOnly",
                    permission: "GET",
                    permissionType: "allow",
                }),
            ]);
            expect(JSON.parse(item.userPermissions.S)).toEqual([]);
        }
    });

    test("enabled: the EDH constraints take the indices after the core ones, which keep theirs", () => {
        // Each Custom::AWS logical id and physical id is derived from the constraint's position, so
        // every core constraint has to keep the index it has without the add-on.
        const base = indexedConstraints(withoutEdhPayloads, "basicro");
        const extended = indexedConstraints(withEdhPayloads, "basicro");
        expect(base.length).toBeGreaterThan(0);
        expect(extended.slice(0, base.length)).toEqual(base);
        expect(extended.slice(base.length)).toEqual(EDH_READ_ONLY_CONSTRAINT_IDS);
    });

    test("disabled: no EDH value and no EDH object type is seeded", () => {
        // Control: the same detectors find the EDH values and types with the add-on on.
        expect(allValues(withEdh).filter((v) => v.includes("/edh")).length).toBeGreaterThan(0);
        expect(edhTier2(withEdh).length).toBeGreaterThan(0);
        expect(allValues(withoutEdh).filter((v) => v.includes("/edh"))).toEqual([]);
        expect(edhTier2(withoutEdh)).toEqual([]);
    });
});
