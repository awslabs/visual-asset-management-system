/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense } from "react";

const Monaco = React.lazy(() => import("@monaco-editor/react"));

interface ConfigEditorProps {
    value: string;
    language: string;
    readOnly?: boolean;
    onChange?: (value: string | undefined) => void;
    height?: string;
}

const ConfigEditor: React.FC<ConfigEditorProps> = ({
    value,
    language,
    readOnly = false,
    onChange,
    height = "400px",
}) => {
    // Map language
    let monacoLanguage = language;
    if (language === "openjd") {
        monacoLanguage = "yaml";
    } else if (language === "raw") {
        monacoLanguage = "plaintext";
    }

    return (
        <Suspense
            fallback={
                <div
                    className="flex items-center justify-center bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                    style={{ height }}
                >
                    Loading editor...
                </div>
            }
        >
            <Monaco
                height={height}
                language={monacoLanguage}
                value={value}
                onChange={onChange}
                theme="vs-dark"
                options={{
                    readOnly,
                    minimap: { enabled: false },
                    lineNumbers: "on",
                    scrollBeyondLastLine: false,
                }}
            />
        </Suspense>
    );
};

export default ConfigEditor;
