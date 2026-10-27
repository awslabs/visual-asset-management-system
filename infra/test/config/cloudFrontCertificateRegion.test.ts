/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The CloudFront custom domain certificate check throws, so its message carries the
 * "Configuration Error:" prefix every other rejection in getConfig() uses.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

function resolve(mutate: (c: any) => void): () => Config.Config {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    mutate(config);
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

const customDomain = (certificateArn: string) => (c: any) => {
    c.app.useCloudFront.enabled = true;
    c.app.useCloudFront.customDomain = {
        enabled: true,
        domainHost: "vams.example.com",
        certificateArn,
    };
};

describe("CloudFront custom domain certificate Region", () => {
    test("a us-east-1 certificate is accepted (control)", () => {
        expect(
            resolve(
                customDomain(
                    "arn:aws:acm:us-east-1:123456789012:certificate/0123abcd-0123-abcd-0123-0123456789ab"
                )
            )
        ).not.toThrow();
    });

    test("a certificate outside us-east-1 throws a Configuration Error", () => {
        expect(
            resolve(
                customDomain(
                    "arn:aws:acm:us-west-2:123456789012:certificate/0123abcd-0123-abcd-0123-0123456789ab"
                )
            )
        ).toThrow(
            /^Configuration Error: CloudFront custom domain certificate ARN should be in us-east-1/
        );
    });
});
