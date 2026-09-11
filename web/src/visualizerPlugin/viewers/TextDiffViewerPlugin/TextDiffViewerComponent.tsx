/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import FormField from "@cloudscape-design/components/form-field";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import Select, { SelectProps } from "@cloudscape-design/components/select";
import Spinner from "@cloudscape-design/components/spinner";
import Toggle from "@cloudscape-design/components/toggle";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import { docco, vs2015 } from "react-syntax-highlighter/dist/esm/styles/hljs";
import json from "react-syntax-highlighter/dist/esm/languages/hljs/json";
import xml from "react-syntax-highlighter/dist/esm/languages/hljs/xml";
import plaintext from "react-syntax-highlighter/dist/esm/languages/hljs/plaintext";
import htmlbars from "react-syntax-highlighter/dist/esm/languages/hljs/htmlbars";
import yaml from "react-syntax-highlighter/dist/esm/languages/hljs/yaml";
import ini from "react-syntax-highlighter/dist/esm/languages/hljs/ini";
import { downloadAsset } from "../../../services/APIService";
import { fetchFileVersions } from "../../../services/AssetVersionService";
import { ViewerPluginProps, FileInfo } from "../../core/types";
import { fileIdentity } from "../../core/fileIdentity";
import { deriveCrossAsset } from "../../core/compareShape";
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

type Side = "left" | "right";
const SIDES: Side[] = ["left", "right"];

/** Sentinel option value for "latest" (no pinned versionId). */
const LATEST_VERSION = "__latest__";

/**
 * Why one side could not be loaded. Each compare entry is fetched under its OWN asset and authorized
 * independently (Casbin, per asset), so a failure is a property of that side, not of the comparison.
 */
type SideErrorKind = "forbidden" | "archived" | "unavailable" | "failed";

interface SideError {
    kind: SideErrorKind;
    message: string;
}

/** One selectable version of a side's file, as returned by fetchFileVersions. */
interface FileVersionOption {
    versionId: string;
    lastModified?: string;
    isLatest?: boolean;
    isArchived?: boolean;
}

interface SideState {
    /** The entry as currently pinned: `versionId` undefined = latest. Replaced (new object) when the
     *  user picks a version, which is what re-triggers that side's fetch. */
    file: FileInfo;
    status: "loading" | "ready" | "error";
    content: string;
    error: SideError | null;
    versions: FileVersionOption[];
    versionsStatus: "loading" | "ready" | "error";
}

type Sides = Record<Side, SideState>;

/** A download that the API rejected, carrying the HTTP status so the side can be classified. */
class DownloadFailure extends Error {
    status?: number;
    constructor(message: string, status?: number) {
        super(message);
        this.name = "DownloadFailure";
        this.status = status;
    }
}

/** Translate a failed fetch into what the user should see for that side. Generic wording on
 *  purpose: the backend's denial reason is not echoed. */
const classifyFailure = (error: unknown): SideError => {
    const status = error instanceof DownloadFailure ? error.status : undefined;
    if (status === 401 || status === 403) {
        return {
            kind: "forbidden",
            message: "You are not authorized to view this file.",
        };
    }
    if (status === 410) {
        return {
            kind: "archived",
            message: "This file version has been archived and is not available.",
        };
    }
    if (status === 404) {
        return {
            kind: "unavailable",
            message: "This file or version could not be found.",
        };
    }
    return {
        kind: "failed",
        message: error instanceof Error && error.message ? error.message : "Failed to load file.",
    };
};

/** Download one entry's text under ITS OWN database/asset (never a shared pair). */
const fetchSideText = async (file: FileInfo, signal: AbortSignal): Promise<string> => {
    const response = await downloadAsset({
        assetId: file.assetId,
        databaseId: file.databaseId,
        key: file.key || "",
        versionId: file.versionId,
        downloadType: "assetFile",
    });
    if (response === false || !Array.isArray(response) || response[0] === false) {
        const message =
            Array.isArray(response) && typeof response[1] === "string"
                ? response[1]
                : `Failed to download file: ${file.filename || file.key}`;
        const status = Array.isArray(response) ? (response[2] as number | undefined) : undefined;
        throw new DownloadFailure(message, status);
    }
    const fileResponse = await fetch(response[1], { signal });
    if (!fileResponse.ok) {
        // The presigned URL itself was refused (expired, object gone); classify like the API would.
        throw new DownloadFailure(
            `HTTP error! status: ${fileResponse.status}`,
            fileResponse.status
        );
    }
    return fileResponse.text();
};

/** Short display form of an S3 versionId. */
const shortVersion = (versionId: string): string => versionId.substring(0, 8);

/** Build a short human label for a compare side. Names the owning asset when the two sides live
 *  in different assets, so identical filenames are still distinguishable. */
const sideLabel = (file: FileInfo, crossAsset: boolean): string => {
    const name = file.filename || file.key.split("/").pop() || file.key;
    const version = file.versionId ? `@ ${shortVersion(file.versionId)}` : "(latest)";
    const asset = crossAsset && file.assetId ? ` · ${file.assetId}` : "";
    return `${name} ${version}${asset}`;
};

const initialSide = (file: FileInfo): SideState => ({
    file,
    status: "loading",
    content: "",
    error: null,
    versions: [],
    versionsStatus: "loading",
});

type SetSides = React.Dispatch<React.SetStateAction<Sides | null>>;

/** Patch one side, leaving the other untouched. No-op when sides are not initialized. */
const patchSide = (setSides: SetSides, side: Side, patch: Partial<SideState>) =>
    setSides((prev) => (prev ? { ...prev, [side]: { ...prev[side], ...patch } } : prev));

/**
 * Per-side content load, keyed on that side's pinned entry. Only the side whose entry changed (version
 * picked, selection replaced) re-fetches; the other side's state is untouched. A failure lands on the
 * side that failed and never resets its counterpart.
 */
const useSideContent = (side: Side, file: FileInfo | undefined, setSides: SetSides) => {
    useEffect(() => {
        if (!file) return;
        const abortController = new AbortController();
        const { signal } = abortController;

        patchSide(setSides, side, { status: "loading", error: null });

        fetchSideText(file, signal)
            .then((content) => {
                if (signal.aborted) return;
                patchSide(setSides, side, { status: "ready", content, error: null });
            })
            .catch((error) => {
                if (signal.aborted) return;
                console.error(`Error loading ${side} side of diff:`, error);
                patchSide(setSides, side, {
                    status: "error",
                    content: "",
                    error: classifyFailure(error),
                });
            });

        return () => abortController.abort();
    }, [side, file, setSides]);
};

/**
 * Per-side version list for the picker, loaded once per file identity (db + asset + key) — picking a
 * version must not refetch the list, so the effect is keyed on the identity, not the entry object. A
 * failure here only degrades the picker to "Latest" (+ the pinned version); the diff still loads.
 */
const useSideVersions = (side: Side, file: FileInfo | undefined, setSides: SetSides) => {
    const identity = file ? fileIdentity(file) : undefined;
    const databaseId = file?.databaseId;
    const assetId = file?.assetId;
    const key = file?.key;
    useEffect(() => {
        if (!identity) return;
        let cancelled = false;

        patchSide(setSides, side, { versionsStatus: "loading" });

        fetchFileVersions({ databaseId, assetId, filePath: key })
            .then(([success, response]: any) => {
                if (cancelled) return;
                const versions: FileVersionOption[] =
                    success && Array.isArray(response?.versions) ? response.versions : [];
                patchSide(setSides, side, {
                    versions,
                    versionsStatus: success ? "ready" : "error",
                });
            })
            .catch((error: unknown) => {
                if (cancelled) return;
                console.error(`Error loading ${side} side versions:`, error);
                patchSide(setSides, side, { versionsStatus: "error" });
            });

        return () => {
            cancelled = true;
        };
    }, [side, identity, databaseId, assetId, key, setSides]);
};

const TextDiffViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    compareFiles,
}) => {
    const [sides, setSides] = useState<Sides | null>(null);
    const [depsStatus, setDepsStatus] = useState<"loading" | "ready" | "error">("loading");
    const [depsError, setDepsError] = useState<string | null>(null);
    const [splitView, setSplitView] = useState(true); // true = side-by-side; false = inline
    const [useWordDiff, setUseWordDiff] = useState(false);
    const [theme, setTheme] = useState<"light" | "dark">(() =>
        document.body.classList.contains("awsui-dark-mode") ? "dark" : "light"
    );

    // Sync with the global light/dark theme (mirrors TextViewerPlugin).
    useEffect(() => {
        const observer = new MutationObserver(() => {
            setTheme(document.body.classList.contains("awsui-dark-mode") ? "dark" : "light");
        });
        observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
        return () => observer.disconnect();
    }, []);

    // Load the diff library (own chunk) once; independent of either side's download.
    useEffect(() => {
        let cancelled = false;
        TextDiffDependencyManager.loadDiffViewer()
            .then(() => {
                if (!cancelled) setDepsStatus("ready");
            })
            .catch((error) => {
                if (cancelled) return;
                console.error("Error loading diff library:", error);
                setDepsStatus("error");
                setDepsError(
                    error instanceof Error ? error.message : "Failed to load diff library"
                );
            });
        return () => {
            cancelled = true;
        };
    }, []);

    // (Re)initialize both sides whenever the compare selection changes. Each entry is resolved to its
    // own database/asset here (DynamicViewer already does this; the fallback covers direct callers).
    useEffect(() => {
        const files = compareFiles || [];
        if (files.length < 2) {
            setSides(null);
            return;
        }
        const resolve = (file: FileInfo): FileInfo => ({
            ...file,
            assetId: file.assetId ?? assetId,
            databaseId: file.databaseId ?? databaseId,
        });
        setSides({ left: initialSide(resolve(files[0])), right: initialSide(resolve(files[1])) });
    }, [assetId, databaseId, compareFiles]);

    const leftFile = sides?.left.file;
    const rightFile = sides?.right.file;

    useSideContent("left", leftFile, setSides);
    useSideContent("right", rightFile, setSides);
    useSideVersions("left", leftFile, setSides);
    useSideVersions("right", rightFile, setSides);

    /** Pin one side to a version (undefined = latest). Replaces that side's entry, which re-fetches
     *  only that side. */
    const handleVersionChange = (side: Side, versionId: string | undefined) => {
        setSides((prev) => {
            if (!prev) return prev;
            const current = prev[side];
            if ((current.file.versionId ?? undefined) === versionId) return prev;
            return {
                ...prev,
                [side]: { ...current, file: { ...current.file, versionId } },
            };
        });
    };

    const crossAsset = useMemo(
        () => (leftFile && rightFile ? deriveCrossAsset([leftFile, rightFile]) : false),
        [leftFile, rightFile]
    );

    const isDark = theme === "dark";
    const language = getLanguageFromExtension(
        leftFile?.filename || leftFile?.key || rightFile?.filename || rightFile?.key || ""
    );

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

    /** Options for one side's version picker: "Latest" first, then each known version. A pinned
     *  version missing from the list (list failed, or archived and filtered upstream) is still
     *  offered so the control shows what is actually on screen. */
    const versionOptions = (side: SideState): SelectProps.Options => {
        const options: SelectProps.Option[] = [{ label: "Latest", value: LATEST_VERSION }];
        const seen = new Set<string>();
        side.versions.forEach((version) => {
            if (!version.versionId || seen.has(version.versionId)) return;
            seen.add(version.versionId);
            const details: string[] = [];
            if (version.lastModified) details.push(new Date(version.lastModified).toLocaleString());
            if (version.isLatest) details.push("latest");
            if (version.isArchived) details.push("archived");
            options.push({
                label: shortVersion(version.versionId),
                value: version.versionId,
                description: details.join(" · ") || undefined,
                disabled: version.isArchived === true,
            });
        });
        const pinned = side.file.versionId;
        if (pinned && !seen.has(pinned)) {
            options.push({ label: shortVersion(pinned), value: pinned });
        }
        return options;
    };

    const selectedVersionOption = (side: SideState): SelectProps.Option => {
        const pinned = side.file.versionId;
        if (!pinned) return { label: "Latest", value: LATEST_VERSION };
        return { label: shortVersion(pinned), value: pinned };
    };

    const renderSideHeader = (side: Side, state: SideState) => (
        <div
            key={side}
            data-side={side}
            style={{
                flex: 1,
                minWidth: 0,
                display: "flex",
                alignItems: "flex-end",
                gap: "12px",
                flexWrap: "wrap",
            }}
        >
            <Box variant="strong">
                <span
                    style={{
                        display: "inline-block",
                        maxWidth: "100%",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                    }}
                    title={state.file.key}
                >
                    {sideLabel(state.file, crossAsset)}
                </span>
            </Box>
            <div style={{ minWidth: "220px" }}>
                <FormField label={`${side === "left" ? "Left" : "Right"} version`}>
                    <Select
                        selectedOption={selectedVersionOption(state)}
                        options={versionOptions(state)}
                        onChange={({ detail }) =>
                            handleVersionChange(
                                side,
                                detail.selectedOption.value === LATEST_VERSION
                                    ? undefined
                                    : detail.selectedOption.value
                            )
                        }
                        statusType={state.versionsStatus === "loading" ? "loading" : "finished"}
                        loadingText="Loading versions"
                        errorText="Could not load versions"
                        placeholder="Latest"
                        expandToViewport
                        ariaLabel={`${side === "left" ? "Left" : "Right"} file version`}
                    />
                </FormField>
            </div>
        </div>
    );

    /** One side on its own: used when the other side cannot be shown, so the readable side is still
     *  rendered instead of collapsing the whole comparison into one error. */
    const renderSidePane = (state: SideState) => {
        if (state.status === "loading") {
            return (
                <Box textAlign="center" padding="l">
                    <Spinner />
                    <Box variant="p" margin={{ top: "xs" }}>
                        Loading file...
                    </Box>
                </Box>
            );
        }
        if (state.status === "error" && state.error) {
            const header =
                state.error.kind === "forbidden"
                    ? "Not authorized"
                    : state.error.kind === "archived"
                    ? "Version archived"
                    : state.error.kind === "unavailable"
                    ? "File unavailable"
                    : "Could not load file";
            return (
                <Box padding="m">
                    <Alert
                        type={state.error.kind === "failed" ? "error" : "warning"}
                        header={header}
                    >
                        {state.error.message}
                        {state.error.kind !== "forbidden" && state.versions.length > 0 && (
                            <Box variant="small" padding={{ top: "xs" }}>
                                Pick another version above to compare it instead.
                            </Box>
                        )}
                    </Alert>
                </Box>
            );
        }
        return (
            <SyntaxHighlighter
                language={language}
                style={isDark ? vs2015 : docco}
                showLineNumbers
                customStyle={{
                    margin: 0,
                    background: "transparent",
                    fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
                    fontSize: "12px",
                }}
            >
                {state.content}
            </SyntaxHighlighter>
        );
    };

    // ---- Render -----------------------------------------------------------------------------

    if (!sides) {
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
                    Text Diff needs exactly two files to compare.
                </div>
            </div>
        );
    }

    if (depsStatus === "error") {
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
                    {depsError || "Diff library could not be loaded."}
                </div>
            </div>
        );
    }

    const bothLoading = SIDES.every((side) => sides[side].status === "loading");
    if (depsStatus === "loading" || bothLoading) {
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

    const bothReady = SIDES.every((side) => sides[side].status === "ready");
    const DiffViewer = TextDiffDependencyManager.getDiffViewer();
    const DiffMethod = TextDiffDependencyManager.getDiffMethod();

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
                    selectedId={splitView ? "split" : "inline"}
                    onChange={({ detail }) => setSplitView(detail.selectedId === "split")}
                    label="Diff layout"
                    options={[
                        { id: "split", text: "Side-by-side" },
                        { id: "inline", text: "Inline" },
                    ]}
                />
                <Toggle
                    checked={useWordDiff}
                    onChange={({ detail }) => setUseWordDiff(detail.checked)}
                >
                    Word-level diff
                </Toggle>
            </div>

            {/* Per-side identity + version pickers */}
            <div
                style={{
                    padding: "8px 16px",
                    borderBottom: "1px solid var(--vams-border-default)",
                    backgroundColor: "var(--vams-bg-primary)",
                    display: "flex",
                    gap: "24px",
                    flexWrap: "wrap",
                }}
            >
                {SIDES.map((side) => renderSideHeader(side, sides[side]))}
            </div>

            {/* Diff, or per-side panes when a side cannot be shown */}
            <div style={{ flex: 1, overflow: "auto" }}>
                {bothReady ? (
                    <DiffViewer
                        oldValue={sides.left.content}
                        newValue={sides.right.content}
                        splitView={splitView}
                        useDarkTheme={isDark}
                        leftTitle={sideLabel(sides.left.file, crossAsset)}
                        rightTitle={sideLabel(sides.right.file, crossAsset)}
                        compareMethod={useWordDiff ? DiffMethod.WORDS : DiffMethod.LINES}
                        renderContent={renderHighlightedContent}
                    />
                ) : (
                    <div style={{ display: "flex", height: "100%" }}>
                        {SIDES.map((side, index) => (
                            <div
                                key={side}
                                style={{
                                    flex: 1,
                                    minWidth: 0,
                                    overflow: "auto",
                                    borderLeft:
                                        index > 0
                                            ? "1px solid var(--vams-border-default)"
                                            : undefined,
                                }}
                            >
                                {renderSidePane(sides[side])}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};

export default TextDiffViewerComponent;
