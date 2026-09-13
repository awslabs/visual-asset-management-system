/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Helpers for asserting on a synthesized Step Functions definition and on the lambda environment
 * that has to agree with it.
 */

import * as fs from "fs";
import { Template } from "aws-cdk-lib/assertions";

/**
 * The ASL document as an object, with CloudFormation tokens replaced so it parses as JSON. Every
 * token in a CDK-rendered definition sits inside a JSON string value, so a placeholder keeps the
 * document well-formed.
 */
export const parseAsl = (properties: any): any => {
    const definition = properties.DefinitionString;
    if (typeof definition === "string") {
        return JSON.parse(definition);
    }
    const [separator, parts] = definition["Fn::Join"] as [string, any[]];
    return JSON.parse(parts.map((p) => (typeof p === "string" ? p : "CFN_TOKEN")).join(separator));
};

/** The Properties of the single state machine in the template. */
export const singleStateMachine = (template: Template): any => {
    const machines = Object.values(template.findResources("AWS::StepFunctions::StateMachine"));
    expect(machines).toHaveLength(1);
    return (machines[0] as any).Properties;
};

/** Environment maps of every lambda in the template whose Handler is exactly `handler`. */
export const lambdaEnvironmentsByHandler = (
    template: Template,
    handler: string
): Record<string, any>[] =>
    (Object.values(template.findResources("AWS::Lambda::Function")) as any[])
        .filter((fn) => fn.Properties.Handler === handler)
        .map((fn) => fn.Properties.Environment?.Variables ?? {});

/**
 * The logical id a job-definition-name derivation refers to, or undefined for any other value.
 *
 * `EcsJobDefinition.jobDefinitionName` and `jobDefinitionNameFromRef` both render
 * `Fn::Select 1 (Fn::Split "/" (Fn::Select 5 (Fn::Split ":" Ref)))`. A raw Ref is the ARN with a
 * revision, and a Batch stream prefix built from it matches nothing.
 */
export const jobDefinitionRefOf = (value: any): string | undefined => {
    const outer = value?.["Fn::Select"];
    if (!Array.isArray(outer) || outer[0] !== 1) return undefined;
    const bySlash = outer[1]?.["Fn::Split"];
    if (!Array.isArray(bySlash) || bySlash[0] !== "/") return undefined;
    const inner = bySlash[1]?.["Fn::Select"];
    if (!Array.isArray(inner) || inner[0] !== 5) return undefined;
    const byColon = inner[1]?.["Fn::Split"];
    if (!Array.isArray(byColon) || byColon[0] !== ":") return undefined;
    return byColon[1]?.Ref;
};

/**
 * The state names a registering lambda emits, read off its module-level `*_STATE_NAME = "..."`
 * literals (a trailing `# comment` is allowed). The producer and the ASL are joined by these
 * strings and nothing else.
 */
export const declaredStageNames = (pythonFile: string): string[] => {
    const source = fs.readFileSync(pythonFile, "utf-8");
    // `[ \t]*` rather than `\s*`: under the `m` flag `\s` would also cross a line break.
    return Array.from(source.matchAll(/^[A-Z_]*STATE_NAME = "([^"]+)"[ \t]*(?:#.*)?$/gm)).map(
        (m) => m[1]
    );
};
