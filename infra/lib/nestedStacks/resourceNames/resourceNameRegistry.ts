/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** One fixed-name resource contributed by a producing nested stack. */
export interface ResourceNameDescriptor {
    paramKey: string; // canonical key from RESOURCE_PARAM_KEYS, e.g. "dynamoTables/assetStorage"
    value: string; // deploy-time token (tableName / bucketName / logGroupName)
}

// Alphanumeric segments separated by forward slashes. Keeps every key a valid SSM
// parameter path segment and guarantees the derived construct IDs (slashes replaced
// with hyphens) can never collide between two distinct keys.
const PARAM_KEY_PATTERN = /^[a-zA-Z0-9]+(\/[a-zA-Z0-9]+)+$/;

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
 */
export class ResourceNameRegistry {
    private readonly descriptors: ResourceNameDescriptor[] = [];
    private readonly seen = new Set<string>();

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

    has(paramKey: string): boolean {
        return this.seen.has(paramKey);
    }

    list(): ResourceNameDescriptor[] {
        return [...this.descriptors];
    }
}
