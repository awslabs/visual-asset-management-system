/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * EDH Workstations helpers for the T1 synth harness (test/support/templateSynth.ts): config hybrids
 * that turn the add-on and its optional features on in a shipped template, and selectors for the
 * resources the add-on's own nested template emits.
 */

import { ACCOUNT, Resource, SynthResult, handlerOf } from "../../../support/templateSynth";

/**
 * EDH Workstations hybrid. The shipped templates set `useEdhWorkstations.enabled: false`, so the
 * add-on stack, its security group and its `/addon/edh/*` routes are never emitted from a shipped
 * config. The endpoint and secret ARN are synth-only values in the shapes `getConfig()` accepts: an
 * `.internal` host over https, and a complete Secrets Manager ARN (6-character suffix) in the
 * template's own partition and region. The VPC is required because the add-on Lambdas are placed in it
 * whenever the add-on is enabled; `useForAllLambdas` is left as shipped (false) so the placement under
 * test is the feature-driven one.
 * Containers stay disabled here; `enableEdhContainers` is the hybrid that turns them on.
 */
export function enableEdh(c: any): void {
    c.app.useGlobalVpc.enabled = true;
    const edh = c.app.addons.useEdhWorkstations;
    edh.enabled = true;
    edh.edhApiEndpoint = "https://internal-edh.example.internal";
    edh.edhAdminApiKeySecretArn = `arn:${c.env.partition}:secretsmanager:${c.env.region}:${ACCOUNT}:secret:edh-root-AbCdEf`;
    edh.edhApiCaBundleSecretArn = "";
    edh.edhApiTlsServerName = "";
    edh.edhClusterId = "edh-t1";
    edh.autoProvisionUsers = true;
    edh.edhAllowedInstanceTypes = ["g5.xlarge", "g5.2xlarge"];
    edh.vdi.defaultProject = "default";
    edh.vdi.virtualDesktopProfileId = 1;
    edh.containers.enabled = false;
}

/**
 * EDH Workstations hybrid with the containers feature on and every container list empty: the
 * container service Lambda and its routes are emitted and no image is built.
 */
export function enableEdhContainers(c: any): void {
    enableEdh(c);
    const containers = c.app.addons.useEdhWorkstations.containers;
    containers.enabled = true;
    containers.buildAndPushImages = [];
    containers.existingEcrImages = [];
    containers.jobDefinitions = [];
    containers.eksEnabled = false;
}

/**
 * EDH Workstations hybrid with AMI builds on: the recipe service, the registrar, the build instance
 * profile and security group, the completion rule and the sweep are emitted, and the shipped recipes
 * are seeded with buildOnDeploy off.
 */
export function enableEdhAmiBuilds(c: any): void {
    enableEdh(c);
    const edh = c.app.addons.useEdhWorkstations;
    edh.amiBuilds.enabled = true;
    edh.amiBuilds.includeShippedRecipes = true;
    edh.amiBuilds.buildSubnetId = "";
    edh.predefinedImages.amiRecipes = [];
}

/** Construct id of the EDH Workstations nested stack; the add-on's template keys contain it. */
export const EDH_STACK = "EdhWorkstationsBuilderNestedStack";

export const inEdhStack = (r: Resource): boolean => r.stack.includes(EDH_STACK);

/** Every `handlers.addon.edh.*` Lambda emitted inside the add-on's own nested template. */
export const edhFunctions = (s: SynthResult): Resource[] =>
    s.where(
        "AWS::Lambda::Function",
        (r) => inEdhStack(r) && /^handlers\.addon\.edh\./.test(handlerOf(r))
    );
