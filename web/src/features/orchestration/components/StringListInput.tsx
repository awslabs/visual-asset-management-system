/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";

interface StringListInputProps {
    /** The current list of entries. */
    value: string[];
    onChange: (next: string[]) => void;
    placeholder?: string;
    ariaLabel?: string;
}

/**
 * An add-to list editor: the user types an entry and clicks Add (or presses Enter) to append it,
 * and removes entries individually. Used for input-file filters, whose entries may be whole paths,
 * file names, extensions, or wildcards — so they must be discrete list items, not a comma-delimited
 * string (a comma-delimited field cannot hold a path/name that itself contains a comma and is easy
 * to get wrong).
 */
const StringListInput: React.FC<StringListInputProps> = ({
    value,
    onChange,
    placeholder,
    ariaLabel,
}) => {
    const [draft, setDraft] = useState("");

    const add = () => {
        const entry = draft.trim();
        if (!entry || value.includes(entry)) {
            setDraft("");
            return;
        }
        onChange([...value, entry]);
        setDraft("");
    };

    const remove = (idx: number) => onChange(value.filter((_, i) => i !== idx));

    return (
        <div>
            <div className="flex gap-2">
                <input
                    type="text"
                    aria-label={ariaLabel}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === "Enter") {
                            e.preventDefault();
                            add();
                        }
                    }}
                    placeholder={placeholder}
                    className="orch-outline flex-1 px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                />
                <button
                    type="button"
                    onClick={add}
                    className="orch-outline px-3 py-2 text-sm text-blue-600 dark:text-blue-400 border border-blue-600 dark:border-blue-400 rounded hover:bg-blue-50 dark:hover:bg-blue-900/20"
                >
                    Add
                </button>
            </div>
            {value.length > 0 && (
                <ul className="flex flex-wrap gap-2 mt-2">
                    {value.map((entry, idx) => (
                        <li
                            key={`${entry}-${idx}`}
                            className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded bg-surface-secondary text-text-primary font-mono"
                        >
                            {entry}
                            <button
                                type="button"
                                aria-label={`Remove ${entry}`}
                                onClick={() => remove(idx)}
                                className="text-text-secondary hover:text-red-600 dark:hover:text-red-400"
                            >
                                ×
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

export default StringListInput;
