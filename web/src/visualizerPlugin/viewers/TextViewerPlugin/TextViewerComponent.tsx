/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";
import { Button, SpaceBetween, Toggle, Select, SelectProps } from "@cloudscape-design/components";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import { docco, github } from "react-syntax-highlighter/dist/esm/styles/hljs";
import json from "react-syntax-highlighter/dist/esm/languages/hljs/json";
import xml from "react-syntax-highlighter/dist/esm/languages/hljs/xml";
import plaintext from "react-syntax-highlighter/dist/esm/languages/hljs/plaintext";
import htmlbars from "react-syntax-highlighter/dist/esm/languages/hljs/htmlbars";
import yaml from "react-syntax-highlighter/dist/esm/languages/hljs/yaml";
import ini from "react-syntax-highlighter/dist/esm/languages/hljs/ini";
import { downloadAsset } from "../../../services/APIService";
import { ViewerPluginProps } from "../../core/types";

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
}) => {
    const [state, setState] = useState<TextViewerState>({
        content: "",
        loading: true,
        error: null,
        language: "plaintext",
        showLineNumbers: true,
        wrapLines: true,
        theme: "light",
    });

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
        const loadFile = async () => {
            if (!assetKey) return;

            try {
                setState((prev) => ({ ...prev, loading: true, error: null }));

                console.log("TextViewerComponent loading file:", {
                    assetId,
                    databaseId,
                    key: assetKey,
                    versionId: versionId || "",
                    downloadType: "assetFile",
                });

                const response = await downloadAsset({
                    assetId: assetId,
                    databaseId: databaseId,
                    key: assetKey || "",
                    versionId: versionId || "",
                    downloadType: "assetFile",
                });

                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        throw new Error("Failed to download file");
                    } else {
                        // Fetch the actual file content from the URL
                        const fileResponse = await fetch(response[1]);
                        if (!fileResponse.ok) {
                            throw new Error(`HTTP error! status: ${fileResponse.status}`);
                        }

                        const textContent = await fileResponse.text();
                        const detectedLanguage = getLanguageFromExtension(assetKey);

                        setState((prev) => ({
                            ...prev,
                            content: textContent,
                            language: detectedLanguage,
                            loading: false,
                        }));
                    }
                } else {
                    throw new Error("Invalid response format");
                }
            } catch (error) {
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
    }, [assetId, assetKey, databaseId, versionId]);

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
                <div style={{ color: "#d13212", marginBottom: "10px", fontSize: "16px" }}>
                    Error loading file
                </div>
                <div style={{ color: "#687078", fontSize: "14px" }}>{state.error}</div>
            </div>
        );
    }

    const formattedContent = formatContent(state.content, state.language);
    const selectedTheme = state.theme === "light" ? docco : github;

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                height: "100%",
                backgroundColor: "#fafafa",
            }}
        >
            {/* Controls */}
            <div
                style={{
                    padding: "12px 16px",
                    borderBottom: "1px solid #e9ebed",
                    backgroundColor: "white",
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
                    backgroundColor: state.theme === "light" ? "white" : "#2d3748",
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
