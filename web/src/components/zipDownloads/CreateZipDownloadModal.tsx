/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useMemo, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import RadioGroup from "@cloudscape-design/components/radio-group";
import Select, { SelectProps } from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { createZipDownload } from "../../services/APIService";
import { fetchAllAssetVersions } from "../../services/AssetVersionService";
import Synonyms from "../../synonyms";
import {
    archiveNameError,
    defaultArchiveName,
    describeSelection,
    withArchiveExtension,
    ZIP_MAX_SELECTION_ENTRIES,
    ZipArchiveFormat,
    ZipCompression,
    ZipDownloadJob,
    ZipSelectionResult,
} from "./zipDownloads";

export interface CreateZipDownloadModalProps {
    visible: boolean;
    onDismiss: () => void;
    databaseId: string;
    assetId: string;
    assetName?: string;
    selection: ZipSelectionResult;
    /** The asset version being browsed in the file manager, preselected as the pin. */
    initialAssetVersionId?: string;
    onCreated: (job: ZipDownloadJob) => void;
}

type ContentMode = "current" | "version";

const PREVIEW_LIMIT = 10;

export const CreateZipDownloadModal: React.FC<CreateZipDownloadModalProps> = ({
    visible,
    onDismiss,
    databaseId,
    assetId,
    assetName,
    selection,
    initialAssetVersionId,
    onCreated,
}) => {
    const [contentMode, setContentMode] = useState<ContentMode>("current");
    const [format, setFormat] = useState<ZipArchiveFormat>("zip");
    const [compression, setCompression] = useState<ZipCompression>("store");
    const [archiveName, setArchiveName] = useState("");
    const [versionOptions, setVersionOptions] = useState<SelectProps.Option[]>([]);
    const [versionsLoading, setVersionsLoading] = useState(false);
    const [versionsError, setVersionsError] = useState<string | null>(null);
    const [selectedVersion, setSelectedVersion] = useState<SelectProps.Option | null>(null);
    const [submitting, setSubmitting] = useState(false);
    const [submitError, setSubmitError] = useState<string | null>(null);

    // Every opening starts from the defaults for the current selection.
    useEffect(() => {
        if (!visible) return;
        setContentMode(initialAssetVersionId ? "version" : "current");
        setFormat("zip");
        setCompression("store");
        setArchiveName(defaultArchiveName(assetName, "zip"));
        setSelectedVersion(null);
        setSubmitError(null);
        setSubmitting(false);
    }, [visible, assetName, initialAssetVersionId]);

    // The asset versions load the first time the version picker is shown (and again after a failed
    // load, when the picker is shown again).
    const [versionsLoaded, setVersionsLoaded] = useState(false);
    useEffect(() => {
        setVersionsLoaded(false);
        setVersionOptions([]);
    }, [databaseId, assetId]);
    useEffect(() => {
        if (!visible || contentMode !== "version" || versionsLoaded) {
            return;
        }
        let cancelled = false;
        setVersionsLoading(true);
        setVersionsError(null);
        fetchAllAssetVersions({ databaseId, assetId })
            .then((result: any) => {
                if (cancelled) return;
                if (!Array.isArray(result) || result[0] !== true) {
                    setVersionsError(
                        (Array.isArray(result) && typeof result[1] === "string" && result[1]) ||
                            `The ${Synonyms.asset} versions could not be loaded.`
                    );
                    return;
                }
                const versions: any[] = result[1]?.versions ?? [];
                const options = versions
                    .filter((v) => v && v.Version !== undefined && !v.isArchived)
                    .map((v) => {
                        const id = `${v.Version}`;
                        const tags = [v.versionAlias, v.isCurrent ? "current" : undefined].filter(
                            Boolean
                        ) as string[];
                        return {
                            value: id,
                            label: `Version ${id}`,
                            description: v.Comment || undefined,
                            tags: tags.length ? tags : undefined,
                        } as SelectProps.Option;
                    });
                setVersionOptions(options);
                setVersionsLoaded(true);
            })
            .finally(() => setVersionsLoading(false));
        return () => {
            cancelled = true;
        };
    }, [visible, contentMode, versionsLoaded, databaseId, assetId]);

    // Preselect the browsed version once the options arrive.
    useEffect(() => {
        if (!visible || selectedVersion || !initialAssetVersionId) return;
        const match = versionOptions.find((o) => o.value === `${initialAssetVersionId}`);
        if (match) setSelectedVersion(match);
    }, [visible, versionOptions, initialAssetVersionId, selectedVersion]);

    const nameError = archiveNameError(archiveName);
    const summaryText = describeSelection(selection, `The whole ${Synonyms.asset}`);
    const refused = selection.refused ?? [];
    const hasRefused = refused.length > 0;
    const tooMany = selection.entries.length > ZIP_MAX_SELECTION_ENTRIES;
    const nothingSelected = selection.entries.length === 0;
    const versionMissing = contentMode === "version" && !selectedVersion?.value;
    const canSubmit =
        !hasRefused && !tooMany && !nothingSelected && !versionMissing && !nameError && !submitting;

    const previewEntries = useMemo(
        () => selection.entries.slice(0, PREVIEW_LIMIT),
        [selection.entries]
    );

    const changeFormat = (next: ZipArchiveFormat) => {
        setFormat(next);
        if (next !== "zip") setCompression("store");
        setArchiveName((name) => (name.trim() ? withArchiveExtension(name.trim(), next) : name));
    };

    const submit = async () => {
        if (!canSubmit) return;
        setSubmitting(true);
        setSubmitError(null);
        const body: any = {
            selection: selection.entries.map(({ type, relativePath }) => ({ type, relativePath })),
            archiveFormat: format,
            compression: format === "zip" ? compression : "store",
        };
        if (archiveName.trim()) body.archiveFileName = archiveName.trim();
        if (contentMode === "version" && selectedVersion?.value) {
            body.assetVersionId = selectedVersion.value;
        }
        const result: any = await createZipDownload({ databaseId, assetId, body });
        setSubmitting(false);
        if (!Array.isArray(result) || result[0] !== true) {
            setSubmitError(
                (Array.isArray(result) && result[1]) || "The zip download could not be created."
            );
            return;
        }
        onCreated(result[1] as ZipDownloadJob);
    };

    return (
        <Modal
            visible={visible}
            onDismiss={() => {
                if (!submitting) onDismiss();
            }}
            closeAriaLabel="Close modal"
            header="Download as Zip"
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={onDismiss} disabled={submitting}>
                            Cancel
                        </Button>
                        <Button
                            variant="primary"
                            onClick={submit}
                            loading={submitting}
                            disabled={!canSubmit}
                        >
                            Create zip download
                        </Button>
                    </SpaceBetween>
                </Box>
            }
        >
            <SpaceBetween size="m">
                <FormField label="Selection" description={summaryText}>
                    {!selection.wholeAsset && previewEntries.length > 0 && (
                        <ul aria-label="Selected items" style={{ margin: 0, paddingLeft: "20px" }}>
                            {previewEntries.map((entry) => (
                                <li key={`${entry.type}:${entry.relativePath}`}>
                                    {entry.relativePath}
                                </li>
                            ))}
                            {selection.entries.length > PREVIEW_LIMIT && (
                                <li>{`and ${selection.entries.length - PREVIEW_LIMIT} more`}</li>
                            )}
                        </ul>
                    )}
                </FormField>

                {hasRefused && (
                    <Alert
                        type="error"
                        statusIconAriaLabel="Error"
                        header="This selection can't be downloaded as a zip"
                    >
                        <SpaceBetween size="xs">
                            <span>
                                {`The selection includes ${
                                    refused.length === 1
                                        ? "an archived or deleted item"
                                        : `${refused.length} archived or deleted items`
                                }. Remove ${
                                    refused.length === 1 ? "it" : "them"
                                } from the selection, or restore ${
                                    refused.length === 1 ? "it" : "them"
                                } first.`}
                            </span>
                            <ul
                                aria-label="Archived or deleted items"
                                style={{ margin: 0, paddingLeft: "20px" }}
                            >
                                {refused.slice(0, PREVIEW_LIMIT).map((path) => (
                                    <li key={path}>{path}</li>
                                ))}
                                {refused.length > PREVIEW_LIMIT && (
                                    <li>{`and ${refused.length - PREVIEW_LIMIT} more`}</li>
                                )}
                            </ul>
                        </SpaceBetween>
                    </Alert>
                )}

                {tooMany && (
                    <Alert type="error" statusIconAriaLabel="Error">
                        {`A zip download can hold up to ${ZIP_MAX_SELECTION_ENTRIES.toLocaleString()} selected files and folders, and this selection has ${selection.entries.length.toLocaleString()}. Select their folders instead, or split the selection into several downloads.`}
                    </Alert>
                )}

                <FormField label="Contents">
                    <RadioGroup
                        value={contentMode}
                        onChange={({ detail }) => setContentMode(detail.value as ContentMode)}
                        items={[
                            {
                                value: "current",
                                label: "Current files",
                                description: "The latest version of each selected file.",
                            },
                            {
                                value: "version",
                                label: `An ${Synonyms.asset} version`,
                                description: `The files as recorded in a saved ${Synonyms.asset} version.`,
                            },
                        ]}
                    />
                </FormField>

                {contentMode === "version" && (
                    <FormField
                        label={`${Synonyms.Asset} version`}
                        errorText={versionsError || undefined}
                    >
                        <Select
                            selectedOption={selectedVersion}
                            onChange={({ detail }) => setSelectedVersion(detail.selectedOption)}
                            options={versionOptions}
                            statusType={versionsLoading ? "loading" : "finished"}
                            loadingText="Loading versions"
                            empty="No versions"
                            placeholder="Choose a version"
                            ariaLabel={`${Synonyms.Asset} version`}
                        />
                    </FormField>
                )}

                <FormField label="Format">
                    <RadioGroup
                        value={format}
                        onChange={({ detail }) => changeFormat(detail.value as ZipArchiveFormat)}
                        items={[
                            { value: "zip", label: "ZIP (.zip)" },
                            { value: "tar", label: "TAR (.tar)" },
                            { value: "tar.gz", label: "Compressed TAR (.tar.gz)" },
                        ]}
                    />
                </FormField>

                {format === "zip" && (
                    <FormField label="Compression">
                        <RadioGroup
                            value={compression}
                            onChange={({ detail }) =>
                                setCompression(detail.value as ZipCompression)
                            }
                            items={[
                                {
                                    value: "store",
                                    label: "No compression",
                                    description:
                                        "Fastest. Best for files that are already compressed.",
                                },
                                {
                                    value: "deflate",
                                    label: "Compress (deflate)",
                                    description: "Smaller archive, slower to prepare.",
                                },
                            ]}
                        />
                    </FormField>
                )}

                <FormField
                    label="Archive name"
                    description="Leave empty to use the default name."
                    errorText={nameError}
                >
                    <Input
                        value={archiveName}
                        onChange={({ detail }) => setArchiveName(detail.value)}
                        ariaLabel="Archive name"
                    />
                </FormField>

                <Alert type="info" statusIconAriaLabel="Info">
                    The archive is prepared in the background and appears under My zip downloads.
                    Only you can see and download it until it expires, at the end of the retention
                    period your administrator set; the archive file is then removed automatically.
                </Alert>

                {submitError && (
                    <Alert type="error" statusIconAriaLabel="Error">
                        {submitError}
                    </Alert>
                )}
            </SpaceBetween>
        </Modal>
    );
};

export default CreateZipDownloadModal;
