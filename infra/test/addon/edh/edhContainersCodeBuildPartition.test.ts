/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The in-account CodeBuild image build is supported in the aws and aws-us-gov partitions. An EDH
 * containers configuration that declares an image to build elsewhere is warned about by name, the
 * way the pipeline `useCodeBuild` flags already are, before the partition rule rejects the add-on.
 */

import * as fs from "fs";
import * as Config from "../../../config/config";
import commercialTemplate from "../../../config/config.template.commercial.json";
import govcloudTemplate from "../../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../../config/config.template.eusovereign.json";
import { newTestApp } from "../../support/testApp";

const EDH_BUILD_WARNING =
    /Configuration Warning: app\.addons\.useEdhWorkstations\.containers\.buildAndPushImages/;

const realReadFileSync = jest.requireActual("fs").readFileSync;
jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** Serve `configJson` for config.json; delegate every other path to the real fs. */
const serveConfig = (configJson: unknown) => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (path: string, ...rest: unknown[]) => {
            if (typeof path === "string" && path.endsWith("config.json")) {
                return JSON.stringify(configJson);
            }
            return realReadFileSync(path, ...rest);
        }
    );
};

/** A deployable config derived from a template, with the placeholders getConfig() requires filled. */
const templateFor = (base: unknown, region: string) => {
    const config = JSON.parse(JSON.stringify(base));
    config.env.region = region;
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn =
            "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    }
    return config;
};

// config.ts is imported once and getConfig() re-reads config.json on every call; jest.resetModules()
// must NOT be used here (it would bind a second fs mock that serveConfig never configures).
const loadConfig = (base: unknown, region: string, mutate: (c: any) => void) => {
    const config = templateFor(base, region);
    mutate(config);
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

/** The add-on with the containers feature on and one image to build, in the given partition. */
const withEdhImageBuild = (partition: string, region: string) => (c: any) => {
    c.app.useGlobalVpc.enabled = true;
    const edh = c.app.addons.useEdhWorkstations;
    edh.enabled = true;
    edh.edhApiEndpoint = "https://internal-edh.example.internal";
    edh.edhAdminApiKeySecretArn = `arn:${partition}:secretsmanager:${region}:123456789012:secret:edh-root-AbCdEf`;
    edh.edhApiCaBundleSecretArn = "";
    edh.edhClusterId = "edh-t1";
    edh.autoProvisionUsers = true;
    edh.edhAllowedInstanceTypes = ["g5.xlarge"];
    edh.vdi.defaultProject = "default";
    edh.vdi.virtualDesktopProfileId = 1;
    edh.containers.enabled = true;
    edh.containers.buildAndPushImages = [
        {
            name: "sim-worker",
            dockerfileDir: "backendPipelines/conversion/coordinateTransform/container",
        },
    ];
    edh.containers.existingEcrImages = [];
    edh.containers.jobDefinitions = [];
    edh.containers.eksEnabled = false;
};

describe("the CodeBuild partition warning covers EDH image builds", () => {
    let warn: jest.SpyInstance;

    beforeEach(() => {
        warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    });

    afterEach(() => {
        warn.mockRestore();
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("EU Sovereign: warns by field name before the partition rule rejects the add-on", () => {
        const run = loadConfig(
            eusovereignTemplate,
            "eusc-de-east-1",
            withEdhImageBuild("aws-eusc", "eusc-de-east-1")
        );
        expect(run).toThrow(/aws-eusc/);
        expect(warn).toHaveBeenCalledWith(expect.stringMatching(EDH_BUILD_WARNING));
    });

    test.each([
        ["commercial", commercialTemplate, "aws", "us-east-1"],
        ["govcloud", govcloudTemplate, "aws-us-gov", "us-gov-west-1"],
    ])(
        "%s: a supported partition neither warns nor throws",
        (_label, template, partition, region) => {
            const run = loadConfig(template, region, withEdhImageBuild(partition, region));
            expect(run).not.toThrow();
            expect(warn).not.toHaveBeenCalledWith(expect.stringMatching(EDH_BUILD_WARNING));
        }
    );

    test("EU Sovereign: no image to build, no warning (the partition rule still throws)", () => {
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1", (c: any) => {
            withEdhImageBuild("aws-eusc", "eusc-de-east-1")(c);
            c.app.addons.useEdhWorkstations.containers.buildAndPushImages = [];
        });
        expect(run).toThrow(/aws-eusc/);
        expect(warn).not.toHaveBeenCalledWith(expect.stringMatching(EDH_BUILD_WARNING));
    });

    test("EU Sovereign: add-on disabled, containers.enabled set - no image-build warning", () => {
        // containers.enabled is normalized to false when the add-on is off, so no image is built;
        // the normalization warning is the only advice the operator should see.
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1", (c: any) => {
            withEdhImageBuild("aws-eusc", "eusc-de-east-1")(c);
            c.app.addons.useEdhWorkstations.enabled = false;
        });
        expect(run).not.toThrow();
        expect(warn).not.toHaveBeenCalledWith(expect.stringMatching(EDH_BUILD_WARNING));
        expect(warn).toHaveBeenCalledWith(expect.stringMatching(/containers\.enabled is ignored/));
    });
});
