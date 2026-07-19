/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

interface WorkflowValidationPanelProps {
    validationErrors: string[];
    validationWarnings: string[];
    backendWarnings: string[];
    saveError: string | null;
}

const WorkflowValidationPanel: React.FC<WorkflowValidationPanelProps> = ({
    validationErrors,
    validationWarnings,
    backendWarnings,
    saveError,
}) => {
    const hasAnyMessages =
        saveError ||
        validationErrors.length > 0 ||
        validationWarnings.length > 0 ||
        backendWarnings.length > 0;

    return (
        <div className="border border-gray-300 dark:border-gray-600 rounded p-6 bg-white dark:bg-gray-900">
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Validation</h2>
            <div className="space-y-4">
                {saveError && (
                    <div className="p-3 bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded">
                        <strong>Save Error:</strong> {saveError}
                    </div>
                )}
                {validationErrors.length > 0 && (
                    <div className="text-red-700 dark:text-red-400">
                        <strong>Errors (blocking save):</strong>
                        <ul className="list-disc list-inside">
                            {validationErrors.map((e, i) => (
                                <li key={i}>{e}</li>
                            ))}
                        </ul>
                    </div>
                )}
                {validationWarnings.length > 0 && (
                    <div className="text-orange-700 dark:text-orange-400">
                        <strong>Warnings:</strong>
                        <ul className="list-disc list-inside">
                            {validationWarnings.map((w, i) => (
                                <li key={i}>{w}</li>
                            ))}
                        </ul>
                    </div>
                )}
                {backendWarnings.length > 0 && (
                    <div className="text-orange-700 dark:text-orange-400">
                        <strong>Backend Warnings:</strong>
                        <ul className="list-disc list-inside">
                            {backendWarnings.map((w, i) => (
                                <li key={i}>{w}</li>
                            ))}
                        </ul>
                    </div>
                )}
                {!hasAnyMessages && (
                    <div className="text-green-700 dark:text-green-400">All validations passed</div>
                )}
            </div>
        </div>
    );
};

export default WorkflowValidationPanel;
