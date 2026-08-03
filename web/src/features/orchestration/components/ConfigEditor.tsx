/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Suspense, useCallback, useEffect, useRef, useState } from "react";

// Lazy-load the editor together with the local-Monaco setup (loader.config + workers) so the
// runtime is bundled from `monaco-editor` (same-origin, CSP-safe) rather than fetched from a CDN.
// Keeping the setup inside the lazy import keeps Monaco out of the main bundle.
const Monaco = React.lazy(async () => {
    await import("./monacoSetup");
    return import("@monaco-editor/react");
});

interface ConfigEditorProps {
    value: string;
    language: string;
    readOnly?: boolean;
    onChange?: (value: string | undefined) => void;
    height?: string;
    /**
     * 1-based line to scroll to and select. Used by the log viewer's find-in-log.
     *
     * Applied on every change, not only at mount: stepping between matches has to move an editor
     * that is already on screen.
     */
    startLine?: number;
    /** 1-based column the match starts at, when the caller knows it. */
    startColumn?: number;
    /** Length of the matched text, so the whole match is selected rather than just located. */
    selectionLength?: number;
}

const ConfigEditor: React.FC<ConfigEditorProps> = ({
    value,
    language,
    readOnly = false,
    onChange,
    height = "400px",
    startLine,
    startColumn,
    selectionLength,
}) => {
    // A handle on the Monaco instance, so a new target can be revealed in the SAME editor. Keying the
    // component on the target instead would remount Monaco on every step — slow, and it loses scroll
    // state and selection, which is what made stepping look like it did nothing.
    const editorRef = useRef<any>(null);

    const revealTarget = useCallback(() => {
        const editor = editorRef.current;
        if (!editor || !startLine) return;
        try {
            const column = startColumn && startColumn > 0 ? startColumn : 1;
            const endColumn = selectionLength ? column + selectionLength : column;
            // Select the match (not just place the cursor): a highlighted range is what makes the hit
            // visible, which is the whole point of stepping to it.
            editor.setSelection({
                startLineNumber: startLine,
                startColumn: column,
                endLineNumber: startLine,
                endColumn,
            });
            editor.revealRangeInCenter({
                startLineNumber: startLine,
                startColumn: column,
                endLineNumber: startLine,
                endColumn,
            });
        } catch {
            // A target beyond the document (a stale index against newer text) is not worth failing
            // the editor over.
        }
    }, [startLine, startColumn, selectionLength]);

    // Re-apply whenever the target moves. Without this the first match would highlight and every
    // subsequent step would silently do nothing.
    useEffect(() => {
        revealTarget();
    }, [revealTarget, value]);
    // Follow the app's theme, which toggles `awsui-dark-mode` on <body>.
    const [isDark, setIsDark] = useState(() => document.body.classList.contains("awsui-dark-mode"));
    useEffect(() => {
        const observer = new MutationObserver(() =>
            setIsDark(document.body.classList.contains("awsui-dark-mode"))
        );
        observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
        return () => observer.disconnect();
    }, []);

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
                    className="flex items-center justify-center bg-surface-secondary text-text-primary"
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
                theme={isDark ? "vs-dark" : "vs"}
                onMount={(editor: any) => {
                    editorRef.current = editor;
                    revealTarget();
                }}
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
