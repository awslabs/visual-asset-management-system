/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { Highlight, themes } from "prism-react-renderer";
import { useColorMode } from "@docusaurus/theme-common";
import styles from "../styles.module.css";

interface Props {
    json: string;
    /**
     * Soft warnings to confirm before downloading (e.g. an empty AWS account
     * ID). These do not block the download — the user can proceed anyway.
     */
    downloadWarnings?: string[];
}

/** Download the JSON as config.json. Only runs in a click handler (browser). */
function downloadJson(json: string) {
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "config.json";
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
}

async function copyJson(json: string): Promise<boolean> {
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(json);
            return true;
        }
    } catch {
        /* fall through to legacy path */
    }
    try {
        const textarea = document.createElement("textarea");
        textarea.value = json;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        const ok = document.execCommand("copy");
        document.body.removeChild(textarea);
        return ok;
    } catch {
        return false;
    }
}

export default function OutputPanel({ json, downloadWarnings = [] }: Props) {
    const [copied, setCopied] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const { colorMode } = useColorMode();
    const prismTheme = colorMode === "dark" ? themes.dracula : themes.github;

    const handleCopy = async () => {
        const ok = await copyJson(json);
        if (ok) {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
        }
    };

    const handleDownloadClick = () => {
        if (downloadWarnings.length > 0) {
            setConfirming(true);
            return;
        }
        downloadJson(json);
    };

    const confirmDownload = () => {
        setConfirming(false);
        downloadJson(json);
    };

    return (
        <div>
            {confirming && downloadWarnings.length > 0 && (
                <div
                    className={styles.confirmCard}
                    role="alertdialog"
                    aria-label="Confirm download"
                >
                    <div className={styles.confirmHeader}>
                        <span className={styles.confirmIcon} aria-hidden="true">
                            ⚠
                        </span>
                        <span>Before you download</span>
                    </div>
                    <ul className={styles.confirmList}>
                        {downloadWarnings.map((warning, i) => (
                            <li key={i}>{warning}</li>
                        ))}
                    </ul>
                    <div className={styles.confirmActions}>
                        <button
                            type="button"
                            className={styles.primaryButton}
                            onClick={confirmDownload}
                        >
                            Download anyway
                        </button>
                        <button
                            type="button"
                            className={styles.secondaryButton}
                            onClick={() => setConfirming(false)}
                        >
                            Go back and edit
                        </button>
                    </div>
                </div>
            )}

            <div className={styles.outputActions}>
                <button
                    type="button"
                    className={styles.primaryButton}
                    onClick={handleDownloadClick}
                >
                    Download config.json
                </button>
                <button type="button" className={styles.secondaryButton} onClick={handleCopy}>
                    {copied ? "Copied ✓" : "Copy to clipboard"}
                </button>
            </div>

            <Highlight theme={prismTheme} code={json} language="json">
                {({ className, style, tokens, getLineProps, getTokenProps }) => (
                    <pre className={`${className} ${styles.preview}`} style={style}>
                        {tokens.map((line, i) => (
                            <div key={i} {...getLineProps({ line })}>
                                {line.map((token, key) => (
                                    <span key={key} {...getTokenProps({ token })} />
                                ))}
                            </div>
                        ))}
                    </pre>
                )}
            </Highlight>
        </div>
    );
}
