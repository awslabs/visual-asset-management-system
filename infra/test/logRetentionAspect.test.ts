/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as logs from "aws-cdk-lib/aws-logs";
import { Aspects } from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { LogRetentionAspect } from "../lib/aspects/log-retention.aspect";
import { newTestApp } from "./support/testApp";

/**
 * `LogRetentionAspect` is the single authority for CloudWatch log retention across VAMS: it visits every
 * `AWS::Logs::LogGroup` and assigns `retentionInDays`, overwriting whatever a construct declared.
 *
 * That override is easy to forget, and forgetting it produced a real defect: 27 construct-level
 * `RetentionDays.TEN_YEARS` declarations across 17 files were dead configuration, and ten documentation
 * statements described the ten-year period that never shipped. Nothing failed, because no test asserted
 * what the synthesized template actually contained.
 *
 * These tests assert the emitted template rather than the construct tree, so a construct that declares its
 * own retention cannot silently appear to win, and a future change to the aspect's value is visible.
 */

const RETENTION_DOCUMENTED_IN_DOCS = 365; // ONE_YEAR — see developer/audit-logging.md and architecture/networking.md

describe("LogRetentionAspect", () => {
    test("overwrites a construct-level retention declaration", () => {
        const app = newTestApp();
        const stack = new cdk.Stack(app, "TestStack");

        // Declared TEN_YEARS deliberately: this is the shape that misled readers before.
        new logs.LogGroup(stack, "DeclaresTenYears", {
            retention: logs.RetentionDays.TEN_YEARS,
        });

        Aspects.of(stack).add(new LogRetentionAspect(logs.RetentionDays.ONE_YEAR));

        Template.fromStack(stack).hasResourceProperties("AWS::Logs::LogGroup", {
            RetentionInDays: 365,
        });
    });

    test("applies to every log group in the stack, not only the first", () => {
        const app = newTestApp();
        const stack = new cdk.Stack(app, "TestStack");

        new logs.LogGroup(stack, "GroupA", { retention: logs.RetentionDays.TEN_YEARS });
        new logs.LogGroup(stack, "GroupB", { retention: logs.RetentionDays.ONE_WEEK });
        new logs.LogGroup(stack, "GroupC"); // no declaration at all

        Aspects.of(stack).add(new LogRetentionAspect(logs.RetentionDays.ONE_YEAR));

        const groups = Template.fromStack(stack).findResources("AWS::Logs::LogGroup");
        const values = Object.values(groups).map((g) => g.Properties?.RetentionInDays);

        expect(values).toHaveLength(3);
        expect(values.every((v) => v === 365)).toBe(true);
    });

    test("overrides the CDK default on a log group that declared no retention", () => {
        // A bare CDK LogGroup is not unset — it emits the construct default of TWO_YEARS (731 days). The
        // aspect must override that too, or a construct added without an explicit retention would quietly
        // keep two years while every document says one.
        const app = newTestApp();
        const stack = new cdk.Stack(app, "TestStack");
        new logs.LogGroup(stack, "NoDeclaration");
        Aspects.of(stack).add(new LogRetentionAspect(logs.RetentionDays.ONE_YEAR));

        Template.fromStack(stack).hasResourceProperties("AWS::Logs::LogGroup", {
            RetentionInDays: 365,
        });
    });

    test("a bare log group emits the CDK default of 731 days when the aspect is absent", () => {
        // Negative control, and the reason the assertions above are meaningful: without the aspect the
        // emitted value is 731, not 365. If a bare group ever emitted 365 on its own, every other test
        // here would pass whether or not the aspect ran.
        const app = newTestApp();
        const stack = new cdk.Stack(app, "TestStack");
        new logs.LogGroup(stack, "NoAspect");

        const groups = Template.fromStack(stack).findResources("AWS::Logs::LogGroup");
        const values = Object.values(groups).map((g) => g.Properties?.RetentionInDays);
        expect(values).toEqual([731]);
        expect(values).not.toContain(365);
    });

    test("propagates the configured value rather than hardcoding one year", () => {
        // Guards against an aspect that ignores its constructor argument, which would make the
        // documented "change it at the aspect" instruction false.
        const app = newTestApp();
        const stack = new cdk.Stack(app, "TestStack");
        new logs.LogGroup(stack, "Group", { retention: logs.RetentionDays.ONE_YEAR });
        Aspects.of(stack).add(new LogRetentionAspect(logs.RetentionDays.TEN_YEARS));

        Template.fromStack(stack).hasResourceProperties("AWS::Logs::LogGroup", {
            RetentionInDays: 3653,
        });
    });

    test("the value applied by core-stack matches what the documentation states", () => {
        // Documentation-consistency guard. architecture/networking.md, architecture/security.md and
        // developer/audit-logging.md all state one-year retention. If core-stack's value changes, this
        // fails and points at the pages that need updating — the sweep that was missed last time.
        const app = newTestApp();
        const stack = new cdk.Stack(app, "TestStack");
        new logs.LogGroup(stack, "Group");
        Aspects.of(stack).add(new LogRetentionAspect(logs.RetentionDays.ONE_YEAR));

        Template.fromStack(stack).hasResourceProperties("AWS::Logs::LogGroup", {
            RetentionInDays: RETENTION_DOCUMENTED_IN_DOCS,
        });
    });
});
