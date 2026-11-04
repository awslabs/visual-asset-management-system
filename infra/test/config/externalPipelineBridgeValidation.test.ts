/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `getConfig()` rules for the externally owned pipeline bridge surface.
 *
 *  * `app.pipelines.externalPipelineBridges` — every entry becomes an `events:PutEvents` statement on the
 *    orchestration bus resource policy and its `invokeTargets` land verbatim on the shared workflow role,
 *    so the account id, the role ARN pattern and every target ARN are checked for shape, partition and
 *    wildcards here. A bad value otherwise deploys as a resource-policy or IAM statement wider than the
 *    operator intended.
 *
 *  * `app.pipelines.executionLogReadPrefixes` — used verbatim as IAM log-group resource names on the
 *    executionService read grant. `*`, `/`, anything outside `/aws/` and a mid-string wildcard are
 *    rejected so the grant can never widen to every log group in the account.
 *
 * Every rejection case asserts on the MESSAGE: these configurations have several ways to be invalid at
 * once, so "it throws" would pass while the rule under test did nothing. The validators are exported so
 * the exhaustive cases run without a full `getConfig()`; `getConfig()` itself is exercised for the
 * defaulting and the wiring.
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

/** Builds a config.json from the commercial template, applies `mutate`, and calls getConfig(). */
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

afterEach(() => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    jest.restoreAllMocks();
});

const ACCOUNT = "210987654321";
const DEPLOYMENT_ACCOUNT = "123456789012";
const ROLE = `arn:aws:iam::${ACCOUNT}:role/bridge-*`;
const bridges = (...entries: any[]) =>
    Config.validateExternalPipelineBridges(entries, "aws", DEPLOYMENT_ACCOUNT);
const prefixes = (...entries: any[]) => Config.validateExecutionLogReadPrefixes(entries);

describe("getConfig() wiring and defaults", () => {
    test("the commercial template passes getConfig() unchanged", () => {
        // Control: both validators run on every deployment, so the shipped configuration must pass.
        expect(resolve(() => undefined)).not.toThrow();
    });

    test("an absent externalPipelineBridges defaults to an empty list", () => {
        const config = resolve((c) => delete c.app.pipelines.externalPipelineBridges)();
        expect(config.app.pipelines.externalPipelineBridges).toEqual([]);
    });

    test("an absent executionLogReadPrefixes defaults to the five shipped prefixes", () => {
        const config = resolve((c) => delete c.app.pipelines.executionLogReadPrefixes)();
        expect(config.app.pipelines.executionLogReadPrefixes).toEqual([
            "/aws/vendedlogs/VAMSStateMachine-*",
            "/aws/vendedlogs/VAMSstateMachine-*",
            "/aws/vendedlogs/Pipelines/*",
            "/aws/batch/job",
            "/aws/sagemaker/TrainingJobs",
        ]);
        expect(config.app.pipelines.executionLogReadPrefixes).toEqual([
            ...Config.DEFAULT_EXECUTION_LOG_READ_PREFIXES,
        ]);
    });

    test("the shipped template default equals the getConfig() default", () => {
        // A template that drifts from the constant would silently change the emitted grant.
        expect((commercialTemplate as any).app.pipelines.executionLogReadPrefixes).toEqual([
            ...Config.DEFAULT_EXECUTION_LOG_READ_PREFIXES,
        ]);
        expect((commercialTemplate as any).app.pipelines.externalPipelineBridges).toEqual([]);
    });

    test("getConfig() rejects a bad bridge through the same validator", () => {
        expect(
            resolve((c) => {
                c.app.pipelines.externalPipelineBridges = [
                    { accountId: "123", roleArnPattern: ROLE },
                ];
            })
        ).toThrow(/externalPipelineBridges\[0\]\.accountId must be a 12-digit/);
    });

    test("getConfig() rejects a bad log prefix through the same validator", () => {
        expect(
            resolve((c) => {
                c.app.pipelines.executionLogReadPrefixes = ["*"];
            })
        ).toThrow(/executionLogReadPrefixes\[0\] '\*' must name a log group under \/aws\//);
    });

    test("getConfig() warns when a bridge names the deployment account", () => {
        const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        resolve((c) => {
            c.app.pipelines.externalPipelineBridges = [
                {
                    accountId: DEPLOYMENT_ACCOUNT,
                    roleArnPattern: `arn:aws:iam::${DEPLOYMENT_ACCOUNT}:role/mock-bridge-*`,
                },
            ];
        })();
        expect(warn.mock.calls.flat().join("\n")).toMatch(/not actually cross-account/);
    });
});

describe("validateExternalPipelineBridges accepts", () => {
    test("an empty list", () => {
        expect(() => bridges()).not.toThrow();
    });

    test("an exact role, a role-name prefix, and a role under a path", () => {
        expect(() =>
            bridges(
                { accountId: ACCOUNT, roleArnPattern: `arn:aws:iam::${ACCOUNT}:role/bridge` },
                { accountId: ACCOUNT, roleArnPattern: ROLE },
                {
                    accountId: ACCOUNT,
                    roleArnPattern: `arn:aws:iam::${ACCOUNT}:role/service/bridge-runner`,
                }
            )
        ).not.toThrow();
    });

    test("exact Lambda, SQS and event-bus targets in any Region of the bridge account", () => {
        expect(() =>
            bridges({
                accountId: ACCOUNT,
                roleArnPattern: ROLE,
                invokeTargets: [
                    `arn:aws:lambda:us-west-2:${ACCOUNT}:function:adapter`,
                    `arn:aws:lambda:us-east-1:${ACCOUNT}:function:adapter:live`,
                    `arn:aws:sqs:us-east-1:${ACCOUNT}:bridge-queue`,
                    `arn:aws:sqs:us-east-1:${ACCOUNT}:bridge-queue.fifo`,
                    `arn:aws:events:us-east-1:${ACCOUNT}:event-bus/mock-bridge`,
                ],
            })
        ).not.toThrow();
    });

    test("an entry when the deployment partition is unknown, comparing nothing", () => {
        // getConfig() derives the partition from the Region; an unrecognized Region yields undefined,
        // and the restricted-partition rule reports that on its own. This validator must not turn the
        // same input into a TypeError before that message is reached (D1).
        expect(() =>
            Config.validateExternalPipelineBridges(
                [
                    {
                        accountId: ACCOUNT,
                        roleArnPattern: `arn:aws-us-gov:iam::${ACCOUNT}:role/bridge-*`,
                        invokeTargets: [`arn:aws-cn:events:cn-north-1:${ACCOUNT}:event-bus/b`],
                    },
                ],
                undefined as unknown as string,
                DEPLOYMENT_ACCOUNT
            )
        ).not.toThrow();
    });

    test("a restricted-partition pattern when the deployment is in that partition", () => {
        expect(() =>
            Config.validateExternalPipelineBridges(
                [
                    {
                        accountId: ACCOUNT,
                        roleArnPattern: `arn:aws-us-gov:iam::${ACCOUNT}:role/bridge-*`,
                        invokeTargets: [
                            `arn:aws-us-gov:events:us-gov-west-1:${ACCOUNT}:event-bus/b`,
                        ],
                    },
                ],
                "aws-us-gov",
                DEPLOYMENT_ACCOUNT
            )
        ).not.toThrow();
    });
});

describe("validateExternalPipelineBridges rejects", () => {
    test("a non-array", () => {
        expect(() =>
            Config.validateExternalPipelineBridges({} as any, "aws", DEPLOYMENT_ACCOUNT)
        ).toThrow(/must be an array/);
    });

    test("a non-12-digit account id", () => {
        expect(() => bridges({ accountId: "12345", roleArnPattern: ROLE })).toThrow(
            /accountId must be a 12-digit AWS account ID/
        );
        expect(() => bridges({ accountId: 210987654321, roleArnPattern: ROLE })).toThrow(
            /accountId must be a 12-digit AWS account ID/
        );
    });

    test("a missing role pattern", () => {
        expect(() => bridges({ accountId: ACCOUNT })).toThrow(/roleArnPattern is required/);
        expect(() => bridges({ accountId: ACCOUNT, roleArnPattern: "" })).toThrow(
            /roleArnPattern is required/
        );
    });

    test("a pattern in another partition, naming the mismatch", () => {
        expect(() =>
            bridges({ accountId: ACCOUNT, roleArnPattern: `arn:aws-us-gov:iam::${ACCOUNT}:role/b` })
        ).toThrow(
            /uses partition 'aws-us-gov' which does not match the deployment partition 'aws'/
        );
    });

    test("a pattern whose account differs from the entry's accountId", () => {
        expect(() =>
            bridges({ accountId: ACCOUNT, roleArnPattern: `arn:aws:iam::111111111111:role/b` })
        ).toThrow(/must be an IAM role ARN in account 210987654321/);
    });

    test("a bare role/* and a path/* pattern", () => {
        expect(() =>
            bridges({ accountId: ACCOUNT, roleArnPattern: `arn:aws:iam::${ACCOUNT}:role/*` })
        ).toThrow(/matches every role under a path/);
        expect(() =>
            bridges({ accountId: ACCOUNT, roleArnPattern: `arn:aws:iam::${ACCOUNT}:role/svc/*` })
        ).toThrow(/matches every role under a path/);
    });

    test("a wildcard anywhere but last, and any '?'", () => {
        expect(() =>
            bridges({ accountId: ACCOUNT, roleArnPattern: `arn:aws:iam::${ACCOUNT}:role/*-bridge` })
        ).toThrow(/may carry '\*' only as its final character/);
        expect(() =>
            bridges({ accountId: ACCOUNT, roleArnPattern: `arn:aws:iam::${ACCOUNT}:role/bridge-?` })
        ).toThrow(/must not contain '\?'/);
    });

    test("a user ARN or a non-IAM ARN", () => {
        expect(() =>
            bridges({ accountId: ACCOUNT, roleArnPattern: `arn:aws:iam::${ACCOUNT}:user/alice` })
        ).toThrow(/must be an IAM role ARN/);
        expect(() =>
            bridges({
                accountId: ACCOUNT,
                roleArnPattern: `arn:aws:sts::${ACCOUNT}:assumed-role/b/s`,
            })
        ).toThrow(/must be an IAM role ARN/);
    });

    test("a duplicate accountId + roleArnPattern pair", () => {
        expect(() =>
            bridges(
                { accountId: ACCOUNT, roleArnPattern: ROLE },
                { accountId: ACCOUNT, roleArnPattern: ROLE }
            )
        ).toThrow(/\[1\] repeats accountId/);
    });

    test("invokeTargets that is not an array", () => {
        expect(() =>
            bridges({ accountId: ACCOUNT, roleArnPattern: ROLE, invokeTargets: "arn:aws:..." })
        ).toThrow(/invokeTargets must be an array/);
    });

    test("an invoke target with any wildcard", () => {
        for (const target of [
            `arn:aws:lambda:us-east-1:${ACCOUNT}:function:*`,
            `arn:aws:sqs:us-east-1:${ACCOUNT}:bridge-*`,
            `arn:aws:events:us-east-1:${ACCOUNT}:event-bus/*`,
            `arn:aws:events:*:${ACCOUNT}:event-bus/b`,
        ]) {
            expect(() =>
                bridges({ accountId: ACCOUNT, roleArnPattern: ROLE, invokeTargets: [target] })
            ).toThrow(/contains a wildcard; invoke targets must be exact ARNs/);
        }
    });

    test("an invoke target in another account, another partition, or another service", () => {
        expect(() =>
            bridges({
                accountId: ACCOUNT,
                roleArnPattern: ROLE,
                invokeTargets: [`arn:aws:lambda:us-east-1:111111111111:function:adapter`],
            })
        ).toThrow(
            /must be an exact Lambda function, SQS queue or EventBridge event-bus ARN in account/
        );
        expect(() =>
            bridges({
                accountId: ACCOUNT,
                roleArnPattern: ROLE,
                invokeTargets: [`arn:aws-cn:lambda:cn-north-1:${ACCOUNT}:function:adapter`],
            })
        ).toThrow(/uses partition 'aws-cn' which does not match/);
        expect(() =>
            bridges({
                accountId: ACCOUNT,
                roleArnPattern: ROLE,
                invokeTargets: [`arn:aws:states:us-east-1:${ACCOUNT}:stateMachine:runner`],
            })
        ).toThrow(/must be an exact Lambda function, SQS queue or EventBridge event-bus ARN/);
    });

    test("a duplicate invoke target", () => {
        const target = `arn:aws:events:us-east-1:${ACCOUNT}:event-bus/b`;
        expect(() =>
            bridges({ accountId: ACCOUNT, roleArnPattern: ROLE, invokeTargets: [target, target] })
        ).toThrow(/invokeTargets\[1\] .* is listed more than once/);
    });
});

describe("validateExecutionLogReadPrefixes accepts", () => {
    test("the defaults, an exact group, a nested prefix and a group with '#' and '.'", () => {
        expect(() => prefixes(...Config.DEFAULT_EXECUTION_LOG_READ_PREFIXES)).not.toThrow();
        expect(() =>
            prefixes(
                "/aws/vendedlogs/mock-bridge-runner",
                "/aws/vendedlogs/Pipelines/mock/bridge/*",
                "/aws/lambda/my.function#1"
            )
        ).not.toThrow();
    });
});

describe("validateExecutionLogReadPrefixes rejects", () => {
    test("an empty list or a non-array", () => {
        expect(() => prefixes()).toThrow(/must be a non-empty array/);
        expect(() => Config.validateExecutionLogReadPrefixes("x" as any)).toThrow(
            /must be a non-empty array/
        );
    });

    test.each([
        "*",
        "/",
        "/aws",
        "/aws/",
        "/aws/*",
        "/aws/vendedlogs/*",
        "/aws/batch/",
        "aws/batch/job",
    ])("the whole-namespace or service-wide pattern %s", (prefix) => {
        expect(() => prefixes(prefix)).toThrow(/must name a log group under \/aws\/<service>\//);
    });

    test.each(["/mock-bridge/runner", "/ecs/task", "/var/log/x"])(
        "a group outside /aws/: %s",
        (prefix) => {
            expect(() => prefixes(prefix)).toThrow(
                /must name a log group under \/aws\/<service>\//
            );
        }
    );

    test("a mid-string wildcard and a '?'", () => {
        expect(() => prefixes("/aws/*/job")).toThrow(/may carry '\*' only as its final character/);
        expect(() => prefixes("/aws/batch/j?b")).toThrow(/must not contain '\?'/);
    });

    test("an empty string, a non-string, and an over-long name", () => {
        expect(() => prefixes("")).toThrow(/must be a non-empty string/);
        expect(() => prefixes(42)).toThrow(/must be a non-empty string/);
        expect(() => prefixes("/aws/batch/" + "x".repeat(510))).toThrow(/exceeds 512 characters/);
    });

    test("a duplicate", () => {
        expect(() => prefixes("/aws/batch/job", "/aws/batch/job")).toThrow(
            /\[1\] '\/aws\/batch\/job' is listed more than once/
        );
    });
});
