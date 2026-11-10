/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The listing Lambdas learn which add-ons the deployment builds.
 *
 * The constraint editor's lists (`GET /auth/constraints/permissionObjects`, `GET /auth/routes/api`),
 * the allowed-route check (`GET /auth/routes/api/allowed`) and the constraint and template validation
 * leave out the object types and routes of an add-on that is not deployed. They read the add-on
 * switches AddonBuilderNestedStack enabled from `ADDON_FEATURES_ENABLED`, which the core stack sets on
 * exactly those three Lambdas: empty with every add-on off, and the switches of each add-on that is on.
 * The backend reads the same name (`backend/backend/common/addonFeatures.py`), and its switch names
 * are the ADDON_APP_FEATURES values.
 */

import * as fs from "fs";
import * as path from "path";
import { ADDON_APP_FEATURES, ADDON_FEATURES_ENABLED_ENV } from "../../common/addonAppFeatures";
import {
    ALL_TEMPLATES,
    SynthResult,
    TemplateName,
    handlerOf,
    synthTemplate,
} from "../support/templateSynth";
import { enableEdh } from "./edh/support/edhSynth";
import { addonObjectTypeFeatures } from "./support/addonBackendConstants";
import { enableRouteContributingAddons } from "./support/addonTestHooks";

const LISTING_HANDLERS = [
    "handlers.auth.routes.lambda_handler",
    "handlers.auth.authConstraintsService.lambda_handler",
    "handlers.auth.authConstraintsTemplateService.lambda_handler",
];

const ADDON_FEATURES_PY = path.resolve(
    __dirname,
    "../../../backend/backend/common/addonFeatures.py"
);

// Five full-app synths: the three shipped templates and two commercial hybrids.
jest.setTimeout(600_000);

/** handler -> the value each listing Lambda carries, and every other Lambda that carries one. */
function listingEnvironment(s: SynthResult): {
    listing: Record<string, string | undefined>;
    others: string[];
} {
    const listing: Record<string, string | undefined> = {};
    const others: string[] = [];
    for (const fn of s.ofType("AWS::Lambda::Function")) {
        const value = fn.properties.Environment?.Variables?.[ADDON_FEATURES_ENABLED_ENV];
        const handler = handlerOf(fn);
        if (LISTING_HANDLERS.includes(handler)) {
            expect(listing[handler]).toBeUndefined();
            listing[handler] = value;
        } else if (value !== undefined) {
            others.push(`${fn.stack}/${fn.logicalId}`);
        }
    }
    return { listing, others };
}

const expectEveryListingLambda = (s: SynthResult, value: string) => {
    const { listing, others } = listingEnvironment(s);
    expect(listing).toEqual(Object.fromEntries(LISTING_HANDLERS.map((h) => [h, value])));
    expect(others).toEqual([]);
};

describe("the add-on switches reach the listing Lambdas", () => {
    test.each(ALL_TEMPLATES)(
        "%s as shipped: each of the three carries the variable, empty",
        (name: TemplateName) => {
            expectEveryListingLambda(synthTemplate(name), "");
        }
    );

    test("with EDH Workstations on: its switch", () => {
        expectEveryListingLambda(
            synthTemplate("commercial", { mutate: enableEdh, mutateKey: "edhWorkstationsEnabled" }),
            ADDON_APP_FEATURES.EDH_WORKSTATIONS
        );
    });

    test("with every route-contributing add-on and feature on: every switch, in build order", () => {
        expectEveryListingLambda(
            synthTemplate("commercial", {
                mutate: enableRouteContributingAddons,
                mutateKey: "routeContributingAddons",
            }),
            [
                ADDON_APP_FEATURES.PHYSNA_ADDON,
                ADDON_APP_FEATURES.EDH_WORKSTATIONS,
                ADDON_APP_FEATURES.EDH_CONTAINERS,
                ADDON_APP_FEATURES.EDH_AMI_RECIPES,
            ].join(",")
        );
    });
});

/** The object types of the admin role's seeded add-on constraints in a synth, in seeding order. */
function seededAdminAddonTypes(s: SynthResult): string[] {
    const addonTypes = addonObjectTypeFeatures();
    const seeded: { index: number; type: string }[] = [];
    for (const resource of s.ofType("Custom::AWS")) {
        const text = SynthResult.flatten(resource.properties.Create ?? resource.properties.Update);
        if (!text.includes('"constraintId"')) continue;
        const payload = JSON.parse(text);
        const item = payload.parameters?.Item;
        const index = /_initializationadmin_(\d+)$/.exec(String(payload.physicalResourceId?.id));
        if (!item?.constraintId || !index) continue;
        if (item.objectType.S in addonTypes) {
            seeded.push({ index: Number(index[1]), type: item.objectType.S });
        }
    }
    return seeded.sort((a, b) => a.index - b.index).map((s) => s.type);
}

describe("the admin role's seeded add-on constraints follow the same switches", () => {
    // A seeded constraint on a type the listings do not offer opens in the editor as not offered,
    // so the admin role is seeded a type exactly when the deployment publishes its switch
    // (ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES in backend common/addonConstants.py).
    const published = (s: SynthResult): Set<string> =>
        new Set(
            (listingEnvironment(s).listing[LISTING_HANDLERS[0]] ?? "")
                .split(",")
                .filter((feature) => feature !== "")
        );
    const offeredTypes = (s: SynthResult): string[] => {
        const features = addonObjectTypeFeatures();
        const on = published(s);
        return Object.keys(features).filter((type) => on.has(features[type]));
    };

    test.each([
        ["as shipped", () => synthTemplate("commercial"), []],
        [
            "with EDH Workstations on and container jobs off",
            () =>
                synthTemplate("commercial", {
                    mutate: enableEdh,
                    mutateKey: "edhWorkstationsEnabled",
                }),
            ["edhWorkstation", "edhImage", "edhCapacityReservation"],
        ],
        [
            "with every route-contributing add-on and feature on",
            () =>
                synthTemplate("commercial", {
                    mutate: enableRouteContributingAddons,
                    mutateKey: "routeContributingAddons",
                }),
            ["edhWorkstation", "edhImage", "edhCapacityReservation", "edhContainer"],
        ],
    ])("%s: each offered type is seeded once, and no other", (_label, synth, expected) => {
        const s = synth();
        // Control: the offered set the switches give is the one the case names.
        expect(offeredTypes(s)).toEqual(expect.arrayContaining(expected as string[]));
        expect(offeredTypes(s)).toHaveLength((expected as string[]).length);
        expect(seededAdminAddonTypes(s)).toEqual(expected);
    });
});

describe("the backend reads the same variable and switch names", () => {
    // Read inside each test, so a missing file fails the test that needs it, not the whole file.
    const backendSource = () => fs.readFileSync(ADDON_FEATURES_PY, "utf-8");

    test("the variable name", () => {
        expect(backendSource()).toMatch(
            new RegExp(`^ADDON_FEATURES_ENABLED_ENV = "${ADDON_FEATURES_ENABLED_ENV}"$`, "m")
        );
    });

    test("one ADDON_FEATURE_* constant per ADDON_APP_FEATURES member, and no other", () => {
        const declared = [
            ...backendSource().matchAll(/^ADDON_FEATURE_[A-Z0-9_]+ = "([A-Z0-9_]+)"$/gm),
        ].map((m) => m[1]);
        expect(declared.length).toBeGreaterThan(0);
        expect([...declared].sort()).toEqual(Object.values(ADDON_APP_FEATURES).sort());
    });
});
