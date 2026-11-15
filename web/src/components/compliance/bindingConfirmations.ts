/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the confirmations of a database binding change say, before anything is sent. A database
 * binding reaches every asset of the database without a schema of its own, and the change is
 * applied to their records in the background (docs/concepts/compliance.md, "Schema binding" and
 * "Applying a database binding").
 */

import Synonyms from "../../synonyms";

export const REMOVE_BINDING_HEADER = "Remove the compliance schema binding";
export const REMOVE_BINDING_CONFIRM_LABEL = "Remove binding";

/**
 * Removing the binding removes the compliance record of each asset that inherits it, except a
 * quarantined one: that keeps its quarantine (and its download block) marked as awaiting
 * re-evaluation. An asset override is left in place.
 */
export const removeBindingConfirmation = (schemaName: string, databaseId: string): string =>
    `Remove ${schemaName} from the ${Synonyms.database} ${databaseId}? The removal is applied in the background. Each ${Synonyms.asset} that inherits the binding loses its compliance state; a quarantined ${Synonyms.asset} stays quarantined, with any download block, and is marked as awaiting re-evaluation until it is released or a newly bound schema evaluates it. ${Synonyms.Assets} that bind their own schema keep it.`;

export const bindingChangeHeader = (currentSchema: string | null | undefined): string =>
    currentSchema ? "Change the compliance schema" : "Bind a compliance schema";

export const bindingChangeConfirmLabel = (currentSchema: string | null | undefined): string =>
    currentSchema ? "Change schema" : "Bind schema";

/**
 * Binding or changing the schema: the database, the schema bound now (if any) and the one chosen,
 * then what the change does. A quarantined asset keeps its quarantine until an evaluation under the
 * new binding decides it; with automatic evaluation on, the change queues the evaluations.
 */
export const bindingChangeConfirmation = (
    databaseId: string,
    currentSchema: string | null | undefined,
    newSchema: string
): string =>
    `${
        currentSchema
            ? `Change the compliance schema of the ${Synonyms.database} ${databaseId} from ${currentSchema} to ${newSchema}?`
            : `Bind ${newSchema} to the ${Synonyms.database} ${databaseId}?`
    } The binding reaches every ${Synonyms.asset} of the ${
        Synonyms.database
    } without a schema of its own and is applied in the background. Quarantined ${
        Synonyms.assets
    } stay quarantined until an evaluation under the new binding decides them; with automatic evaluation on, an evaluation of each ${
        Synonyms.asset
    } is queued.`;
