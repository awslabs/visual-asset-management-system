/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Stack } from "aws-cdk-lib";
import { IConstruct } from "constructs";
import { RESOURCE_PARAM_KEYS } from "../../../common/resourceParamKeys";

/** One fixed-name resource contributed by a producing nested stack. */
export interface ResourceNameDescriptor {
    paramKey: string; // canonical key from RESOURCE_PARAM_KEYS, e.g. "dynamoTables/assetStorage"
    value: string; // deploy-time token (tableName / bucketName / logGroupName)
}

/**
 * A resource name an add-on or custom stack published through publishLaneResourceName(), recorded
 * so the chunk parameters carry it too. `owner` is the stack that holds its per-key parameter.
 */
export interface LaneResourceNameEntry {
    paramKey: string;
    value: string;
    lane: "addon" | "custom";
    owner: Stack;
}

/**
 * A core resource name published by a stack built after ResourceNamesBuilder materializes the
 * registry (the OpenSearch endpoint and index names, the web URL, the map API key ARN, the copy
 * engine's function name), through publishLateCoreResourceName() or recordLateCoreResourceName()
 * (lateCoreResourceNames.ts). Recorded so the chunk parameters carry it with the core names.
 * `owner` is the stack that holds its per-key parameter.
 */
export interface LateCoreResourceNameEntry {
    paramKey: string;
    value: string;
    owner: Stack;
}

/** Every core key (RESOURCE_PARAM_KEYS), the only keys recordLateCore() accepts. */
const CORE_KEYS = new Set<string>(
    Object.values(RESOURCE_PARAM_KEYS).flatMap((category) =>
        Object.values(category as Record<string, string>)
    )
);

// Alphanumeric segments separated by forward slashes. Keeps every key a valid SSM
// parameter path segment and guarantees the derived construct IDs (slashes replaced
// with hyphens) can never collide between two distinct keys.
const PARAM_KEY_PATTERN = /^[a-zA-Z0-9]+(\/[a-zA-Z0-9]+)+$/;

// The registry of each deployment, keyed by the construct it was attached to (the core stack), so a
// lane helper finds it from any scope inside that stack without a parameter of its own. Weak, so a
// test process that builds many apps keeps none of them alive.
const ATTACHED = new WeakMap<IConstruct, ResourceNameRegistry>();

/**
 * Cross-stack resource-name registry. Producing stacks register descriptors while they
 * build their resources; the ResourceNames builder reads the full set last and
 * materializes one SSM parameter per entry.
 *
 * Lifecycle across deployments (parameters are explicitly named, DESTROY policy):
 * - Adding a registration creates a new parameter on the next deploy.
 * - Changing a value (e.g. a table replaced) updates the parameter in place.
 * - Renaming a paramKey replaces the parameter (new logical ID and new name, so
 *   create-before-delete cannot collide). Backend callers pick the new key up on
 *   their next cache refresh; keep the old key's env-var alias in
 *   backend/common/resourceNames.py during any transition window.
 * - Removing a registration deletes the parameter on the next deploy.
 *
 * The registry also records the add-on and custom lane names (recordLane(), called by
 * publishLaneResourceName()) and the core names stacks built after ResourceNamesBuilder publish
 * (recordLateCore(), called by lateCoreResourceNames.ts). Both publish their own per-key parameters;
 * the chunk builder (resourceNameChunks.ts) reads all three sets.
 */
export class ResourceNameRegistry {
    private readonly descriptors: ResourceNameDescriptor[] = [];
    private readonly lanes: LaneResourceNameEntry[] = [];
    private readonly lateCore: LateCoreResourceNameEntry[] = [];
    private readonly seen = new Set<string>();
    private readonly seenLane = new Set<string>();
    private readonly seenLateCore = new Set<string>();

    /** Make `registry` the one publishLaneResourceName() finds from any scope inside `scope`. */
    static attach(scope: IConstruct, registry: ResourceNameRegistry): void {
        ATTACHED.set(scope, registry);
    }

    /** The registry attached to `scope` or its nearest ancestor that has one. */
    static of(scope: IConstruct): ResourceNameRegistry | undefined {
        const path = scope.node.scopes;
        for (let i = path.length - 1; i >= 0; i--) {
            const found = ATTACHED.get(path[i]);
            if (found) return found;
        }
        return undefined;
    }

    register(d: ResourceNameDescriptor): void {
        if (!PARAM_KEY_PATTERN.test(d.paramKey)) {
            throw new Error(
                `ResourceNameRegistry: invalid paramKey "${d.paramKey}" — expected alphanumeric ` +
                    `segments separated by "/" (e.g. "dynamoTables/assetStorage")`
            );
        }
        if (!d.value) {
            throw new Error(
                `ResourceNameRegistry: empty value registered for paramKey "${d.paramKey}"`
            );
        }
        if (this.seen.has(d.paramKey)) {
            throw new Error(
                `ResourceNameRegistry: duplicate resource name registered: ${d.paramKey}`
            );
        }
        if (this.seenLateCore.has(d.paramKey)) {
            throw new Error(
                `ResourceNameRegistry: ${d.paramKey} is already published by a later stack; ` +
                    `register it in one place`
            );
        }
        this.seen.add(d.paramKey);
        this.descriptors.push(d);
    }

    /**
     * Record a lane name for the chunk parameters. A key published twice throws: its two per-key
     * parameters would share one SSM name, and the deploy would fail part-way through.
     */
    recordLane(entry: LaneResourceNameEntry): void {
        if (this.seenLane.has(entry.paramKey)) {
            throw new Error(
                `ResourceNameRegistry: ${entry.lane} resource name published twice: ${entry.paramKey}`
            );
        }
        this.seenLane.add(entry.paramKey);
        this.lanes.push(entry);
    }

    /**
     * Record a core name a stack built after ResourceNamesBuilder publishes, for the chunk
     * parameters. Throws on a key that is not a core key, a malformed key, an empty value, a key
     * register() already took (ResourceNamesBuilder publishes its parameter) or a key recorded
     * twice: either way two per-key parameters would share one SSM name, and the deploy would fail
     * part-way through.
     */
    recordLateCore(entry: LateCoreResourceNameEntry): void {
        if (!PARAM_KEY_PATTERN.test(entry.paramKey)) {
            throw new Error(
                `ResourceNameRegistry: invalid paramKey "${entry.paramKey}" — expected alphanumeric ` +
                    `segments separated by "/" (e.g. "openSearch/endpoint")`
            );
        }
        if (!CORE_KEYS.has(entry.paramKey)) {
            throw new Error(
                `ResourceNameRegistry: "${entry.paramKey}" is not a core resource-name key ` +
                    `(RESOURCE_PARAM_KEYS); an add-on or custom name is published through its lane`
            );
        }
        if (!entry.value) {
            throw new Error(
                `ResourceNameRegistry: empty value recorded for paramKey "${entry.paramKey}"`
            );
        }
        if (this.seen.has(entry.paramKey)) {
            throw new Error(
                `ResourceNameRegistry: ${entry.paramKey} is registered with register(), so ` +
                    `ResourceNamesBuilder publishes it; a later stack cannot publish it too`
            );
        }
        if (this.seenLateCore.has(entry.paramKey)) {
            throw new Error(
                `ResourceNameRegistry: core resource name published twice: ${entry.paramKey}`
            );
        }
        this.seenLateCore.add(entry.paramKey);
        this.lateCore.push(entry);
    }

    has(paramKey: string): boolean {
        return this.seen.has(paramKey);
    }

    list(): ResourceNameDescriptor[] {
        return [...this.descriptors];
    }

    /** The lane names recorded so far, in publication order. */
    laneEntries(): LaneResourceNameEntry[] {
        return [...this.lanes];
    }

    /** The late core names recorded so far, in publication order. */
    lateCoreEntries(): LateCoreResourceNameEntry[] {
        return [...this.lateCore];
    }
}
