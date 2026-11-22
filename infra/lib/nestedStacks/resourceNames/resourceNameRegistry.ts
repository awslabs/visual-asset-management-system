/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Stack } from "aws-cdk-lib";
import { IConstruct } from "constructs";

/** One fixed-name resource contributed by a producing nested stack. */
export interface ResourceNameDescriptor {
    paramKey: string; // canonical key from RESOURCE_PARAM_KEYS, e.g. "dynamoTables/assetStorage"
    value: string; // deploy-time token (tableName / bucketName / logGroupName)
    // Longest the deploy-time value can be, for sizing the resource-name chunk parameters
    // (resourceNameChunks.ts). Optional: unset, the chunk builder uses the limit of the key's
    // category (or the literal's own length).
    maxLength?: number;
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
 * publishLaneResourceName()), which publish their own per-key parameters; the chunk builder
 * (resourceNameChunks.ts) reads both sets.
 */
export class ResourceNameRegistry {
    private readonly descriptors: ResourceNameDescriptor[] = [];
    private readonly lanes: LaneResourceNameEntry[] = [];
    private readonly seen = new Set<string>();
    private readonly seenLane = new Set<string>();

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
}
