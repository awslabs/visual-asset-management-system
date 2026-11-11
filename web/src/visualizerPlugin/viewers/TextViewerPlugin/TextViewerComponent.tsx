/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import {
    Alert,
    Button,
    SpaceBetween,
    Toggle,
    Select,
    SelectProps,
} from "@cloudscape-design/components";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import { docco } from "react-syntax-highlighter/dist/esm/styles/hljs";
import { vs2015 } from "react-syntax-highlighter/dist/esm/styles/hljs";
import json from "react-syntax-highlighter/dist/esm/languages/hljs/json";
import xml from "react-syntax-highlighter/dist/esm/languages/hljs/xml";
import plaintext from "react-syntax-highlighter/dist/esm/languages/hljs/plaintext";
import htmlbars from "react-syntax-highlighter/dist/esm/languages/hljs/htmlbars";
import yaml from "react-syntax-highlighter/dist/esm/languages/hljs/yaml";
import ini from "react-syntax-highlighter/dist/esm/languages/hljs/ini";
import { downloadAsset } from "../../../services/APIService";
import { downloadFailureMessage } from "../../core/streamFailure";
import { ViewerPluginProps } from "../../core/types";
import { loadTextFile } from "../../core/textContent";

/** Why the file is not shown as text, when it is not. */
type TextNotice = "binary" | "encoding";

/** User-facing copy for each notice. Exported so the unit test asserts the same strings. */
export const TEXT_NOTICE_COPY: Record<TextNotice, { header: string; body: string }> = {
    binary: {
        header: "Binary file",
        body: "This file appears to contain binary data, so the Text Viewer does not display it. Download the file to open it in an application that supports its format.",
    },
    encoding: {
        header: "Unsupported text encoding",
        body: "This file uses a text encoding the Text Viewer cannot display (UTF-32 or UTF-16 without a byte-order mark).",
    },
};

// Register languages with the light build
SyntaxHighlighter.registerLanguage("json", json);
SyntaxHighlighter.registerLanguage("xml", xml);
SyntaxHighlighter.registerLanguage("plaintext", plaintext);
SyntaxHighlighter.registerLanguage("htmlbars", htmlbars);
SyntaxHighlighter.registerLanguage("yaml", yaml);
SyntaxHighlighter.registerLanguage("ini", ini);

interface TextViewerState {
    content: string;
    loading: boolean;
    error: string | null;
    /** Set instead of `content` when the file is binary or in an encoding the viewer cannot read. */
    notice: TextNotice | null;
    language: string;
    showLineNumbers: boolean;
    wrapLines: boolean;
    theme: "light" | "dark";
}

const TextViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
    assetVersionId,
}) => {
    const [state, setState] = useState<TextViewerState>(() => ({
        content: "",
        loading: true,
        error: null,
        notice: null,
        language: "plaintext",
        showLineNumbers: true,
        wrapLines: true,
        theme: document.body.classList.contains("awsui-dark-mode") ? "dark" : "light",
    }));

    // Watch for global theme changes and sync the text viewer theme
    useEffect(() => {
        const observer = new MutationObserver(() => {
            const isDark = document.body.classList.contains("awsui-dark-mode");
            setState((prev) => ({ ...prev, theme: isDark ? "dark" : "light" }));
        });
        observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
        return () => observer.disconnect();
    }, []);

    // Determine language from file extension
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
                return "ini"; // TOML uses similar syntax to INI
            case "txt":
                return "plaintext";
            default:
                return "plaintext";
        }
    };

    // Load file content
    useEffect(() => {
        const abortController = new AbortController();
        let cancelled = false;

        const loadFile = async () => {
            if (!assetKey) return;

            try {
                setState((prev) => ({ ...prev, loading: true, error: null, notice: null }));

                console.log("TextViewerComponent loading file:", {
                    assetId,
                    databaseId,
                    key: assetKey,
                    assetVersionId: assetVersionId,
                    downloadType: "assetFile",
                });

                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey || "",
                    versionId: versionId,
                    assetVersionId: assetVersionId as any,
                    downloadType: "assetFile",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        throw new Error(
                            downloadFailureMessage(response, "Failed to download file")
                        );
                    } else {
                        // Read the first bytes and stop there when they are not text: a binary file
                        // (an image under an extensionless name, a renamed archive) is never
                        // downloaded whole and never handed to the highlighter.
                        const result = await loadTextFile(response[1], abortController.signal);
                        if (cancelled) return;
                        if (result.kind !== "text") {
                            setState((prev) => ({
                                ...prev,
                                content: "",
                                notice: result.kind === "binary" ? "binary" : "encoding",
                                loading: false,
                            }));
                            return;
                        }
                        const detectedLanguage = getLanguageFromExtension(assetKey);

                        setState((prev) => ({
                            ...prev,
                            content: result.text,
                            notice: null,
                            language: detectedLanguage,
                            loading: false,
                        }));
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
                // A load the user walked away from is not a failure to report, and the component is
                // gone, so there is no state left to update.
                if (cancelled || abortController.signal.aborted) return;
                console.error("Error loading file:", error);
                setState((prev) => ({
                    ...prev,
                    error: error instanceof Error ? error.message : "Failed to load file",
                    loading: false,
                }));
            }
        };

        if (assetKey) {
            loadFile();
        }

        return () => {
            // Closing the viewer mid-download stops the transfer rather than letting it finish for a
            // component that is no longer mounted.
            cancelled = true;
            abortController.abort();
        };
    }, [assetId, assetKey, databaseId, versionId, assetVersionId]);

    // Copy content to clipboard
    const copyToClipboard = async () => {
        try {
            await navigator.clipboard.writeText(state.content);
            // Could add a toast notification here if available
        } catch (err) {
            console.error("Failed to copy text: ", err);
        }
    };

    // Format JSON content
    const formatContent = (content: string, language: string): string => {
        if (language === "json") {
            try {
                return JSON.stringify(JSON.parse(content), null, 2);
            } catch {
                return content; // Return original if parsing fails
            }
        }
        return content;
    };

    // Language options for manual selection
    const languageOptions: SelectProps.Option[] = [
        { label: "Auto-detect", value: getLanguageFromExtension(assetKey || "") },
        { label: "Plain Text", value: "plaintext" },
        { label: "JSON", value: "json" },
        { label: "XML", value: "xml" },
        { label: "HTML", value: "htmlbars" },
        { label: "YAML", value: "yaml" },
        { label: "TOML/INI", value: "ini" },
    ];

    if (state.loading) {
        return (
            <div
                style={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    height: "100%",
                    fontSize: "16px",
                }}
            >
                Loading file content...
            </div>
        );
    }

    if (state.error) {
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
                <div
                    style={{
                        color: "var(--vams-color-error)",
                        marginBottom: "10px",
                        fontSize: "16px",
                    }}
                >
                    Error loading file
                </div>
                <div style={{ color: "var(--vams-text-secondary)", fontSize: "14px" }}>
                    {state.error}
                </div>
            </div>
        );
    }

    if (state.notice) {
        const copy = TEXT_NOTICE_COPY[state.notice];
        return (
            <div
                data-testid={`text-viewer-${state.notice}-notice`}
                style={{ padding: "20px", textAlign: "left" }}
            >
                <Alert type="info" header={copy.header}>
                    {copy.body}
                </Alert>
            </div>
        );
    }

    const formattedContent = formatContent(state.content, state.language);
    const selectedTheme = state.theme === "light" ? docco : vs2015;

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
                }}
            >
                <SpaceBetween direction="horizontal" size="m">
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <span style={{ fontSize: "14px", fontWeight: "500" }}>Language:</span>
                        <Select
                            selectedOption={{
                                label:
                                    state.language === "plaintext"
                                        ? "Plain Text"
                                        : state.language === "htmlbars"
                                        ? "HTML"
                                        : state.language === "ini"
                                        ? "TOML/INI"
                                        : state.language.toUpperCase(),
                                value: state.language,
                            }}
                            onChange={({ detail }) =>
                                setState((prev) => ({
                                    ...prev,
                                    language: detail.selectedOption.value || "plaintext",
                                }))
                            }
                            options={languageOptions}
                            placeholder="Select language"
                        />
                    </div>

                    <Toggle
                        onChange={({ detail }) =>
                            setState((prev) => ({ ...prev, showLineNumbers: detail.checked }))
                        }
                        checked={state.showLineNumbers}
                    >
                        Line numbers
                    </Toggle>

                    <Toggle
                        onChange={({ detail }) =>
                            setState((prev) => ({ ...prev, wrapLines: detail.checked }))
                        }
                        checked={state.wrapLines}
                    >
                        Wrap lines
                    </Toggle>

                    <Toggle
                        onChange={({ detail }) =>
                            setState((prev) => ({
                                ...prev,
                                theme: detail.checked ? "dark" : "light",
                            }))
                        }
                        checked={state.theme === "dark"}
                    >
                        Dark theme
                    </Toggle>

                    <Button iconName="copy" variant="normal" onClick={copyToClipboard}>
                        Copy
                    </Button>
                </SpaceBetween>
            </div>

            {/* Content */}
            <div
                style={{
                    flex: 1,
                    overflow: "auto",
                    backgroundColor: state.theme === "light" ? "var(--vams-bg-primary)" : "#1e1e1e",
                }}
            >
                <SyntaxHighlighter
                    language={state.language}
                    style={selectedTheme}
                    showLineNumbers={state.showLineNumbers}
                    wrapLines={state.wrapLines}
                    wrapLongLines={state.wrapLines}
                    customStyle={{
                        margin: 0,
                        padding: "16px",
                        backgroundColor: "transparent",
                        fontSize: "14px",
                        lineHeight: "1.5",
                        fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
                        textAlign: "left",
                        whiteSpace: state.wrapLines ? "pre-wrap" : "pre",
                        overflowX: state.wrapLines ? "hidden" : "auto",
                    }}
                >
                    {formattedContent}
                </SyntaxHighlighter>
            </div>
        </div>
    );
};

export default TextViewerComponent;
