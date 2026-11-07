/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import Modal from "@cloudscape-design/components/modal";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table, { TableProps } from "@cloudscape-design/components/table";
import { useNavigate } from "react-router-dom";
import {
    deleteZipDownload,
    fetchAssetZipDownloads,
    fetchZipDownloads,
    getZipDownloadUrl,
} from "../../services/APIService";
import { useAllowedRoutes } from "../../features/orchestration/permissions/useAllowedRoutes";
import { routeDisabledReason } from "../../features/orchestration/permissions/routeDisabledReason";
import Synonyms from "../../synonyms";
import {
    describeSelection,
    expiresIn,
    formatBytes,
    formatTimestamp,
    isTerminalStatus,
    mergeZipJobs,
    progressPercent,
    STATUS_INDICATOR,
    STATUS_LABELS,
    triggerBrowserDownload,
    ZIP_DISABLED_MESSAGE,
    ZIP_PAGE_SIZE,
    ZIP_ROUTES,
    zipRefetchInterval,
    ZipDownloadJob,
} from "./zipDownloads";

export interface ZipDownloadsTableProps {
    /** Scope the list to one asset; omitted, the table lists the caller's jobs across assets. */
    databaseId?: string;
    assetId?: string;
    /** False while the surface is hidden (an inactive tab): nothing loads or polls. */
    isActive?: boolean;
    /** Header variant: the global page shows an h1, the asset tab an h2. */
    headerVariant?: "h1" | "h2";
    /** Bumped by the parent to force a reload (a job was just created). */
    refreshToken?: number;
}

interface Notice {
    type: "success" | "error" | "info";
    text: string;
}

type ConfirmKind = "cancel" | "delete";

const useDocumentVisible = (): boolean => {
    const [visible, setVisible] = useState(
        typeof document === "undefined" ? true : document.visibilityState !== "hidden"
    );
    useEffect(() => {
        const onChange = () => setVisible(document.visibilityState !== "hidden");
        document.addEventListener("visibilitychange", onChange);
        return () => document.removeEventListener("visibilitychange", onChange);
    }, []);
    return visible;
};

const StatusCell: React.FC<{ job: ZipDownloadJob }> = ({ job }) => {
    const percent = job.status === "RUNNING" ? progressPercent(job) : null;
    return (
        <SpaceBetween size="xxs">
            <StatusIndicator type={STATUS_INDICATOR[job.status] ?? "info"}>
                {STATUS_LABELS[job.status] ?? job.status}
            </StatusIndicator>
            {job.status === "RUNNING" && percent !== null && (
                <ProgressBar
                    value={percent}
                    ariaLabel={`Progress of ${job.archiveFileName}`}
                    additionalInfo={
                        job.progress?.filesTotal
                            ? `${job.progress?.filesDone ?? 0} of ${job.progress.filesTotal} files`
                            : undefined
                    }
                />
            )}
            {job.status === "FAILED" && job.errorMessage && (
                <Box variant="small" color="text-status-error">
                    {job.errorMessage}
                </Box>
            )}
            {job.status === "CANCELLED" && job.cancelledBy === "execution" && (
                <Box variant="small" color="text-body-secondary">
                    Cancelled from workflow executions
                </Box>
            )}
        </SpaceBetween>
    );
};

const SelectionCell: React.FC<{ job: ZipDownloadJob }> = ({ job }) => {
    const summary = describeSelection(job.selectionSummary, `Whole ${Synonyms.asset}`);
    const preview = job.selectionPreview ?? [];
    const pinned = job.assetVersionId ? ` (version ${job.assetVersionId})` : "";
    if (job.selectionSummary?.wholeAsset || preview.length === 0) {
        return <span>{`${summary}${pinned}`}</span>;
    }
    return (
        <ExpandableSection headerText={`${summary}${pinned}`} variant="footer">
            <ul style={{ margin: 0, paddingLeft: "20px" }}>
                {preview.map((entry) => (
                    <li key={`${entry.type}:${entry.relativePath}:${entry.versionId ?? ""}`}>
                        {entry.relativePath}
                    </li>
                ))}
            </ul>
        </ExpandableSection>
    );
};

export const ZipDownloadsTable: React.FC<ZipDownloadsTableProps> = ({
    databaseId,
    assetId,
    isActive = true,
    headerVariant = "h2",
    refreshToken = 0,
}) => {
    const navigate = useNavigate();
    const assetScoped = !!(databaseId && assetId);
    const documentVisible = useDocumentVisible();
    const { loading: permissionsLoading, failed: permissionsFailed, can } = useAllowedRoutes();

    const canList = can("GET", assetScoped ? ZIP_ROUTES.asset : ZIP_ROUTES.global);
    const canDownload = can("POST", ZIP_ROUTES.url);
    const canRemove = can("DELETE", ZIP_ROUTES.request);
    const downloadReason = routeDisabledReason(
        permissionsLoading,
        !!permissionsFailed,
        canDownload,
        "Your role does not allow downloading zip downloads."
    );
    const removeReason = routeDisabledReason(
        permissionsLoading,
        !!permissionsFailed,
        canRemove,
        "Your role does not allow cancelling or deleting zip downloads."
    );

    const [jobs, setJobs] = useState<ZipDownloadJob[]>([]);
    const [nextToken, setNextToken] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [loadingMore, setLoadingMore] = useState(false);
    const [loaded, setLoaded] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [disabled, setDisabled] = useState(false);
    const [notice, setNotice] = useState<Notice | null>(null);
    const [busyId, setBusyId] = useState<string | null>(null);
    const [confirm, setConfirm] = useState<{ job: ZipDownloadJob; kind: ConfirmKind } | null>(null);
    const [confirming, setConfirming] = useState(false);
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    const fetchPage = useCallback(
        (startingToken?: string | null) =>
            assetScoped
                ? fetchAssetZipDownloads({
                      databaseId,
                      assetId,
                      pageSize: ZIP_PAGE_SIZE,
                      maxItems: ZIP_PAGE_SIZE,
                      startingToken,
                  })
                : fetchZipDownloads({
                      pageSize: ZIP_PAGE_SIZE,
                      maxItems: ZIP_PAGE_SIZE,
                      startingToken,
                  }),
        [assetScoped, databaseId, assetId]
    );

    /** Load the first page. `quiet` (a poll) keeps the rows and their later pages on screen. */
    const load = useCallback(
        async (quiet = false) => {
            if (!quiet) setLoading(true);
            const result: any = await fetchPage(null);
            if (!mounted.current) return;
            if (!quiet) setLoading(false);
            if (!Array.isArray(result) || result[0] !== true) {
                const message = (Array.isArray(result) && result[1]) || "";
                if (result?.[2] === 404 && `${message}`.includes(ZIP_DISABLED_MESSAGE)) {
                    setDisabled(true);
                } else if (!quiet) {
                    setLoadError(message || "Zip downloads could not be loaded.");
                }
                return;
            }
            const page = result[1] as { Items: ZipDownloadJob[]; NextToken: string | null };
            setLoadError(null);
            setDisabled(false);
            setLoaded(true);
            if (quiet) {
                setJobs((current) => mergeZipJobs(current, page.Items));
            } else {
                setJobs(page.Items);
                setNextToken(page.NextToken);
            }
        },
        [fetchPage]
    );

    // Load when the surface becomes active, and again when the parent asks.
    useEffect(() => {
        if (!isActive || permissionsLoading || !canList) return;
        load(false);
    }, [isActive, permissionsLoading, canList, load, refreshToken]);

    // Poll only while a job is queued or running, the surface is active and the tab is visible.
    const interval = zipRefetchInterval(jobs, isActive && canList, documentVisible);
    useEffect(() => {
        if (interval === null) return;
        const timer = setTimeout(() => load(true), interval);
        return () => clearTimeout(timer);
    }, [interval, jobs, load]);

    const loadMore = async () => {
        if (!nextToken) return;
        setLoadingMore(true);
        const result: any = await fetchPage(nextToken);
        if (!mounted.current) return;
        setLoadingMore(false);
        if (!Array.isArray(result) || result[0] !== true) {
            setNotice({
                type: "error",
                text: result?.[1] || "More zip downloads could not be loaded.",
            });
            return;
        }
        const page = result[1] as { Items: ZipDownloadJob[]; NextToken: string | null };
        setJobs((current) => mergeZipJobs(current, page.Items));
        setNextToken(page.NextToken);
    };

    const download = async (job: ZipDownloadJob) => {
        setBusyId(job.requestId);
        setNotice(null);
        const result: any = await getZipDownloadUrl({
            databaseId: job.databaseId,
            assetId: job.assetId,
            requestId: job.requestId,
        });
        if (!mounted.current) return;
        setBusyId(null);
        if (!Array.isArray(result) || result[0] !== true) {
            setNotice({
                type: "error",
                text: `Download of ${job.archiveFileName} failed: ${
                    result?.[1] || "the download link could not be created."
                }`,
            });
            return;
        }
        triggerBrowserDownload(result[1].url, result[1].archiveFileName || job.archiveFileName);
    };

    const runConfirm = async () => {
        if (!confirm) return;
        const { job, kind } = confirm;
        setConfirming(true);
        const result: any = await deleteZipDownload({
            databaseId: job.databaseId,
            assetId: job.assetId,
            requestId: job.requestId,
        });
        if (!mounted.current) return;
        setConfirming(false);
        setConfirm(null);
        if (!Array.isArray(result) || result[0] !== true) {
            setNotice({
                type: "error",
                text: `${kind === "cancel" ? "Cancel" : "Delete"} of ${
                    job.archiveFileName
                } failed: ${result?.[1] || "the request was not accepted."}`,
            });
            return;
        }
        const action = result[1]?.action;
        if (action === "deleted") {
            setJobs((current) => current.filter((j) => j.requestId !== job.requestId));
            setNotice({ type: "success", text: `${job.archiveFileName} was deleted.` });
        } else {
            setNotice({ type: "success", text: `${job.archiveFileName} was cancelled.` });
        }
        load(true);
    };

    const columns: TableProps.ColumnDefinition<ZipDownloadJob>[] = [
        {
            id: "createdAt",
            header: "Requested",
            cell: (job) => formatTimestamp(job.createdAt),
        },
        ...(assetScoped
            ? []
            : [
                  {
                      id: "asset",
                      header: Synonyms.Asset,
                      cell: (job: ZipDownloadJob) => (
                          <Link
                              href={`#/databases/${job.databaseId}/assets/${job.assetId}`}
                              onFollow={(event) => {
                                  event.preventDefault();
                                  navigate(`/databases/${job.databaseId}/assets/${job.assetId}`);
                              }}
                          >
                              {job.assetName || job.assetId}
                          </Link>
                      ),
                  },
              ]),
        {
            id: "archive",
            header: "Archive",
            cell: (job) => job.archiveFileName,
        },
        {
            id: "selection",
            header: "Selection",
            cell: (job) => <SelectionCell job={job} />,
        },
        {
            id: "format",
            header: "Format",
            cell: (job) =>
                job.archiveFormat === "zip" && job.compression === "deflate"
                    ? "zip (compressed)"
                    : job.archiveFormat,
        },
        {
            id: "size",
            header: "Size",
            cell: (job) =>
                job.status === "SUCCEEDED"
                    ? formatBytes(job.archiveSizeBytes)
                    : formatBytes(job.progress?.bytesTotal || null),
        },
        {
            id: "status",
            header: "Status",
            cell: (job) => <StatusCell job={job} />,
        },
        {
            id: "expires",
            header: "Expires",
            cell: (job) => expiresIn(job.expiresAt),
        },
        {
            id: "actions",
            header: "Actions",
            cell: (job) => {
                const terminal = isTerminalStatus(job.status);
                return (
                    <SpaceBetween direction="horizontal" size="xs">
                        {job.status === "SUCCEEDED" && (
                            <Button
                                iconName="download"
                                onClick={() => download(job)}
                                loading={busyId === job.requestId}
                                disabled={!!downloadReason}
                                disabledReason={downloadReason}
                                ariaLabel={`Download ${job.archiveFileName}`}
                            >
                                Download
                            </Button>
                        )}
                        <Button
                            onClick={() =>
                                setConfirm({ job, kind: terminal ? "delete" : "cancel" })
                            }
                            disabled={!!removeReason}
                            disabledReason={removeReason}
                            ariaLabel={`${terminal ? "Delete" : "Cancel"} ${job.archiveFileName}`}
                        >
                            {terminal ? "Delete" : "Cancel"}
                        </Button>
                    </SpaceBetween>
                );
            },
        },
    ];

    if (!permissionsLoading && !permissionsFailed && !canList) {
        return (
            <Alert type="info" statusIconAriaLabel="Info" header="Zip downloads are not available">
                Your role does not allow listing zip downloads.
            </Alert>
        );
    }

    if (disabled) {
        return (
            <Alert type="info" statusIconAriaLabel="Info" header="Zip downloads are not available">
                {ZIP_DISABLED_MESSAGE}.
            </Alert>
        );
    }

    return (
        <SpaceBetween size="m">
            {notice && (
                <Alert
                    type={notice.type}
                    dismissible
                    onDismiss={() => setNotice(null)}
                    dismissAriaLabel="Dismiss message"
                    statusIconAriaLabel={notice.type}
                >
                    {notice.text}
                </Alert>
            )}
            {loadError && (
                <Alert
                    type="error"
                    statusIconAriaLabel="Error"
                    header="Zip downloads could not be loaded"
                    action={<Button onClick={() => load(false)}>Retry</Button>}
                >
                    {loadError}
                </Alert>
            )}
            <Table
                trackBy="requestId"
                items={jobs}
                columnDefinitions={columns}
                loading={loading || permissionsLoading || (!loaded && !loadError && isActive)}
                loadingText="Loading zip downloads"
                wrapLines
                variant={assetScoped ? "embedded" : "container"}
                header={
                    <Header
                        variant={headerVariant}
                        counter={loaded ? `(${jobs.length}${nextToken ? "+" : ""})` : undefined}
                        description={
                            assetScoped
                                ? `Archives you requested for this ${Synonyms.asset}. Only you can see them.`
                                : `Archives you requested across all ${Synonyms.assets}. Only you can see them.`
                        }
                        actions={
                            <Button
                                iconName="refresh"
                                ariaLabel="Refresh zip downloads"
                                onClick={() => load(false)}
                                disabled={loading}
                            />
                        }
                    >
                        My zip downloads
                    </Header>
                }
                empty={
                    <Box textAlign="center" color="inherit" padding="m">
                        <b>No zip downloads</b>
                        <Box variant="p" color="inherit">
                            {`Select files or folders in the File Manager and choose Export, then Download as Zip.`}
                        </Box>
                    </Box>
                }
                footer={
                    nextToken ? (
                        <Box textAlign="center">
                            <Button onClick={loadMore} loading={loadingMore}>
                                Load more
                            </Button>
                        </Box>
                    ) : undefined
                }
            />
            <Modal
                visible={!!confirm}
                onDismiss={() => {
                    if (!confirming) setConfirm(null);
                }}
                closeAriaLabel="Close modal"
                header={confirm?.kind === "cancel" ? "Cancel zip download" : "Delete zip download"}
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                variant="link"
                                onClick={() => setConfirm(null)}
                                disabled={confirming}
                            >
                                {confirm?.kind === "cancel" ? "Keep it" : "Keep"}
                            </Button>
                            <Button variant="primary" onClick={runConfirm} loading={confirming}>
                                {confirm?.kind === "cancel" ? "Cancel download" : "Delete"}
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                {confirm?.kind === "cancel"
                    ? `Stop preparing ${confirm.job.archiveFileName}? The partial archive is discarded.`
                    : confirm
                    ? `Delete ${confirm.job.archiveFileName}? The archive is removed and cannot be recovered.`
                    : null}
            </Modal>
        </SpaceBetween>
    );
};

export default ZipDownloadsTable;
