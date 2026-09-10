/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Box, SegmentedControl, Spinner, Toggle } from "@cloudscape-design/components";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import json from "react-syntax-highlighter/dist/esm/languages/hljs/json";
import xml from "react-syntax-highlighter/dist/esm/languages/hljs/xml";
import plaintext from "react-syntax-highlighter/dist/esm/languages/hljs/plaintext";
import htmlbars from "react-syntax-highlighter/dist/esm/languages/hljs/htmlbars";
import yaml from "react-syntax-highlighter/dist/esm/languages/hljs/yaml";
import ini from "react-syntax-highlighter/dist/esm/languages/hljs/ini";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps, FileInfo } from "../../core/types";
import { TextDiffDependencyManager } from "./dependencies";

// Register languages with the light build (mirrors TextViewerPlugin).
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("xml", xml);
SyntaxHighlighter.registerLanguage("plaintext", plaintext);
SyntaxHighlighter.registerLanguage("htmlbars", htmlbars);
SyntaxHighlighter.registerLanguage("yaml", yaml);
SyntaxHighlighter.registerLanguage("ini", ini);

/** Map a filename extension to a react-syntax-highlighter language (mirrors TextViewerPlugin). */
const getLanguageFromExtension = (filename: string): string => {
    if (!filename) return "plaintext";
    const ext = filename.toLowerCase().split(".").pop();
    switch (ext) {
        case "json":
            return "json";
        case "xml":
            return "xml";
        case "html":
        case "htm":
            return "htmlbars";
        case "yaml":
        case "yml":
            return "yaml";
        case "toml":
            return "ini";
        default:
            return "plaintext";
    }
};

interface FileSide {
    file: FileInfo;
    content: string;
    label: string;
}

interface TextDiffViewerState {
    left: FileSide | null;
    right: FileSide | null;
    loading: boolean;
    error: string | null;
    depsLoaded: boolean;
    splitView: boolean; // true = side-by-side (split); false = inline/overlay
    useWordDiff: boolean;
    theme: "light" | "dark";
}

/** Resolve a file's owning asset/database (Decision #3): prefer the file's own pair, fall back to
 *  the shared top-level pair. */
const resolveContext = (
    file: FileInfo,
    topAssetId: string,
    topDatabaseId: string
): { assetId: string; databaseId: string } => ({
    assetId: file.assetId ?? topAssetId,
    databaseId: file.databaseId ?? topDatabaseId,
});

/** Build a short human label for a compare side from its file + versionId. */
const sideLabel = (file: FileInfo): string => {
    const name = file.filename || file.key.split("/").pop() || file.key;
    if (file.versionId) {
        return `${name} @ ${file.versionId.substring(0, 8)}`;
    }
    return `${name} (current)`;
};

const TextDiffViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    compareFiles,
}) => {
    const [state, setState] = useState<TextDiffViewerState>(() => ({
        left: null,
        right: null,
        loading: true,
        error: null,
        depsLoaded: false,
        splitView: true,
        useWordDiff: false,
        theme: document.body.classList.contains("awsui-dark-mode") ? "dark" : "light",
    }));

    // Sync with the global light/dark theme (mirrors TextViewerPlugin).
    useEffect(() => {
        const observer = new MutationObserver(() => {
            const isDark = document.body.classList.contains("awsui-dark-mode");
            setState((prev) => ({ ...prev, theme: isDark ? "dark" : "light" }));
        });
        observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
        return () => observer.disconnect();
    }, []);

    // Load the diff library and both files' text content.
    useEffect(() => {
        const abortController = new AbortController();
        let cancelled = false;

        const fetchText = async (file: FileInfo): Promise<string> => {
            const ctx = resolveContext(file, assetId, databaseId);
            const response = await downloadAsset({
                assetId: ctx.assetId,
                databaseId: ctx.databaseId,
                key: file.key || "",
                versionId: file.versionId,
                downloadType: "assetFile",
            });
            if (response === false || !Array.isArray(response) || response[0] === false) {
                throw new Error(`Failed to download file: ${file.filename || file.key}`);
            }
            const fileResponse = await fetch(response[1], { signal: abortController.signal });
            if (!fileResponse.ok) {
                throw new Error(`HTTP error! status: ${fileResponse.status}`);
            }
            return fileResponse.text();
        };

        const load = async () => {
            const files = compareFiles || [];
            if (files.length < 2) {
                setState((prev) => ({
                    ...prev,
                    loading: false,
                    error: "Text Diff needs exactly two files to compare.",
                }));
                return;
            }
            const [leftFile, rightFile] = files;

            try {
                setState((prev) => ({ ...prev, loading: true, error: null }));

                // Dynamically load the diff library (own chunk) alongside the two downloads.
                const [, leftContent, rightContent] = await Promise.all([
                    TextDiffDependencyManager.loadDiffViewer(),
                    fetchText(leftFile),
                    fetchText(rightFile),
                ]);

                if (cancelled) return;

                setState((prev) => ({
                    ...prev,
                    left: { file: leftFile, content: leftContent, label: sideLabel(leftFile) },
                    right: { file: rightFile, content: rightContent, label: sideLabel(rightFile) },
                    depsLoaded: true,
                    loading: false,
                }));
            } catch (error) {
                if (cancelled || abortController.signal.aborted) return;
                console.error("Error loading diff:", error);
                setState((prev) => ({
                    ...prev,
                    error: error instanceof Error ? error.message : "Failed to load diff",
                    loading: false,
                }));
            }
        };

        load();

        return () => {
            cancelled = true;
            abortController.abort();
        };
    }, [assetId, databaseId, compareFiles]);

    if (state.loading) {
        return (
            <div
                style={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    height: "100%",
                }}
            >
                <Box textAlign="center">
                    <Spinner size="large" />
                    <Box variant="p" margin={{ top: "s" }}>
                        Loading diff...
                    </Box>
                </Box>
            </div>
        );
    }

    if (state.error || !state.left || !state.right || !state.depsLoaded) {
        return (
            <div
                style={{
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "center",
                    alignItems: "center",
                    height: "100%",
                    padding: "20px",
                    textAlign: "center",
                }}
            >
                <div style={{ color: "var(--vams-color-error)", fontSize: "16px" }}>
                    Error loading diff
                </div>
                <div style={{ color: "var(--vams-text-secondary)", fontSize: "14px" }}>
                    {state.error || "Diff could not be rendered."}
                </div>
            </div>
        );
    }

    const DiffViewer = TextDiffDependencyManager.getDiffViewer();
    const DiffMethod = TextDiffDependencyManager.getDiffMethod();
    const language = getLanguageFromExtension(state.left.file.filename || state.left.file.key);
    const isDark = state.theme === "dark";

    // Syntax highlighting hook for the diff viewer: each line's text is highlighted with
    // react-syntax-highlighter (already a project dependency), keeping the highlighter out of the
    // base bundle via the same Light build TextViewerPlugin uses.
    const renderHighlightedContent = (source: string): React.ReactNode => (
        <SyntaxHighlighter
            language={language}
            // PreTag/CodeTag as spans so the diff viewer's own line layout is preserved.
            PreTag="span"
            CodeTag="span"
            customStyle={{
                display: "inline",
                background: "transparent",
                padding: 0,
                margin: 0,
                fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
            }}
        >
            {source}
        </SyntaxHighlighter>
    );

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                height: "100%",
                backgroundColor: "var(--vams-bg-secondary)",
            }}
        >
            {/* Controls */}
            <div
                style={{
                    padding: "12px 16px",
                    borderBottom: "1px solid var(--vams-border-default)",
                    backgroundColor: "var(--vams-bg-primary)",
                    display: "flex",
                    alignItems: "center",
                    gap: "16px",
                    flexWrap: "wrap",
                }}
            >
                <SegmentedControl
                    selectedId={state.splitView ? "split" : "inline"}
                    onChange={({ detail }) =>
                        setState((prev) => ({ ...prev, splitView: detail.selectedId === "split" }))
                    }
                    label="Diff layout"
                    options={[
                        { id: "split", text: "Side-by-side" },
                        { id: "inline", text: "Inline" },
                    ]}
                />
                <Toggle
                    checked={state.useWordDiff}
                    onChange={({ detail }) =>
                        setState((prev) => ({ ...prev, useWordDiff: detail.checked }))
                    }
                >
                    Word-level diff
                </Toggle>
            </div>

            {/* Diff */}
            <div style={{ flex: 1, overflow: "auto" }}>
                <DiffViewer
                    oldValue={state.left.content}
                    newValue={state.right.content}
                    splitView={state.splitView}
                    useDarkTheme={isDark}
                    leftTitle={state.left.label}
                    rightTitle={state.right.label}
                    compareMethod={state.useWordDiff ? DiffMethod.WORDS : DiffMethod.LINES}
                    renderContent={renderHighlightedContent}
                />
            </div>
        </div>
    );
};

export default TextDiffViewerComponent;
