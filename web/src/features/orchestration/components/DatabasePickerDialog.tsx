/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import Dialog from "./Dialog";
import { useDatabases } from "../api/queries";

interface DatabasePickerDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    /** Called with the chosen databaseId when the user confirms. */
    onSelect: (databaseId: string) => void;
}

/**
 * Picks a target database before creating a pipeline/workflow from the global (database-less)
 * list pages. Pipelines and workflows are database-scoped, so a create action launched from the
 * global page needs a destination database first.
 */
const DatabasePickerDialog: React.FC<DatabasePickerDialogProps> = ({
    open,
    onOpenChange,
    title,
    onSelect,
}) => {
    const { data: databases = [], isLoading, error } = useDatabases(open);
    const [selected, setSelected] = useState("");

    return (
        <Dialog
            open={open}
            onOpenChange={onOpenChange}
            title={title}
            footer={
                <>
                    <button
                        onClick={() => onOpenChange(false)}
                        className="orch-outline px-4 py-2 rounded border border-border-default text-text-primary hover:bg-surface-hover"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={() => selected && onSelect(selected)}
                        disabled={!selected}
                        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        Continue
                    </button>
                </>
            }
        >
            {isLoading ? (
                <p className="text-text-secondary">Loading databases…</p>
            ) : error ? (
                <p className="text-vams-error">Failed to load databases.</p>
            ) : (
                <label className="block">
                    <span className="block text-sm font-medium mb-1 text-text-primary">
                        Database
                    </span>
                    <select
                        value={selected}
                        onChange={(e) => setSelected(e.target.value)}
                        className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                    >
                        <option value="">Select a database…</option>
                        {/* GLOBAL is a valid create target (cross-database pipelines/workflows) but is
                            not a real database record returned by useDatabases, so offer it explicitly. */}
                        <option value="GLOBAL">GLOBAL (shared across all databases)</option>
                        {databases.map((db) => (
                            <option key={db.databaseId} value={db.databaseId}>
                                {db.databaseId}
                                {db.description ? ` — ${db.description}` : ""}
                            </option>
                        ))}
                    </select>
                </label>
            )}
        </Dialog>
    );
};

export default DatabasePickerDialog;
