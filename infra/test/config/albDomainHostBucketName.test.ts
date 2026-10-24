/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * In ALB mode `app.useAlb.domainHost` names two Amazon S3 buckets, the web app bucket and
 * `<domainHost>-webappaccesslogs`, so getConfig() and the ConfigBuilder reject a value that is not a
 * bucket name of 3 to 46 characters and name the field in the error.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const DOMAIN_HOST_ERROR =
    /^Configuration Error: app\.useAlb\.domainHost names the ALB web app bucket/;

function albConfig(domainHost: string): any {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    config.app.useCloudFront.enabled = false;
    config.app.useGlobalVpc.enabled = true;
    config.app.useAlb.enabled = true;
    config.app.useAlb.domainHost = domainHost;
    config.app.useAlb.certificateArn =
        "arn:aws:acm:us-east-1:123456789012:certificate/0123abcd-0123-abcd-0123-0123456789ab";
    return config;
}

function resolve(config: any): () => Config.Config {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

const builderFlags = (config: any) =>
    evaluateRules(config).some((r) => r.id === "alb-domain-bucket-name");

describe("ALB domainHost bucket-name validation", () => {
    test.each(["vams.example.com", "a".repeat(46)])("%s is accepted (control)", (host) => {
        expect(resolve(albConfig(host))).not.toThrow(DOMAIN_HOST_ERROR);
        expect(builderFlags(albConfig(host))).toBe(false);
    });

    test.each([
        ["47 characters", "a".repeat(47)],
        ["uppercase letters", "VAMS.example.com"],
        ["a leading hyphen", "-vams.example.com"],
        ["a trailing dot", "vams.example.com."],
        ["an underscore", "vams_app.example.com"],
    ])("a domainHost with %s is rejected", (_why, host) => {
        expect(resolve(albConfig(host))).toThrow(DOMAIN_HOST_ERROR);
        expect(builderFlags(albConfig(host))).toBe(true);
    });
});
