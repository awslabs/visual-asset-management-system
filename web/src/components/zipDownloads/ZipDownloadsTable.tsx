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
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import TextFilter from "@cloudscape-design/components/text-filter";
import { useNavigate } from "react-router-dom";
import {
    deleteZipDownload,
    fetchAssetZipDownloads,
    fetchAssetZipDownloadsAllUsers,
    fetchZipDownloads,
    getZipDownloadUrl,
} from "../../services/APIService";
import { useAllowedRoutes } from "../../features/orchestration/permissions/useAllowedRoutes";
import { routeDisabledReason } from "../../features/orchestration/permissions/routeDisabledReason";
import Synonyms from "../../synonyms";
import {
    describeSelection,
    expiresIn,
    filterZipJobs,
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
    /** Header variant of the table's own header (the global page carries the page's h1). */
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
    // "Show all users" is offered only when the API's route listing allows the all-users route; the
    // API still checks the route and the asset on each call.
    const canListAllUsers = assetScoped && !permissionsLoading && can("GET", ZIP_ROUTES.allUsers);
    const [scope, setScope] = useState<"mine" | "all">("mine");
    const showAllUsers = canListAllUsers && scope === "all";
    const canDownload = can("POST", ZIP_ROUTES.url);
    const canRemove = can("DELETE", ZIP_ROUTES.request);
    const downloadReason = routeDisabledReason(
        permissionsLoading,
        !!permissionsFailed,
        canDownload,
        "Your role does not allow downloading archives from the Download Queue."
    );
    const removeReason = routeDisabledReason(
        permissionsLoading,
        !!permissionsFailed,
        canRemove,
        "Your role does not allow cancelling or deleting download jobs."
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
    const [filterText, setFilterText] = useState("");
    const mounted = useRef(true);
    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    const fetchPage = useCallback(
        (startingToken?: string | null) =>
            assetScoped && showAllUsers
                ? fetchAssetZipDownloadsAllUsers({
                      databaseId,
                      assetId,
                      pageSize: ZIP_PAGE_SIZE,
                      maxItems: ZIP_PAGE_SIZE,
                      startingToken,
                  })
                : assetScoped
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
        [assetScoped, showAllUsers, databaseId, assetId]
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
                    setLoadError(message || "The Download Queue could not be loaded.");
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
                text: result?.[1] || "More download jobs could not be loaded.",
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

    // The job behind an open cancel confirm as the latest poll shows it. A job that finished while
    // the dialog was open is not cancelled: the dialog says so and offers no confirm (#1125 D23).
    const confirmJob = confirm
        ? jobs.find((j) => j.requestId === confirm.job.requestId) ?? confirm.job
        : null;
    const finishedWhileOpen =
        confirm?.kind === "cancel" && !!confirmJob && isTerminalStatus(confirmJob.status);

    const runConfirm = async () => {
        if (!confirm || finishedWhileOpen) return;
        const { job, kind } = confirm;
        setConfirming(true);
        const target = {
            databaseId: job.databaseId,
            assetId: job.assetId,
            requestId: job.requestId,
        };
        // A cancel carries the cancel intent, so the API never deletes a job that finished
        // after this dialog last saw it; it keeps it and answers "ended".
        const result: any = await deleteZipDownload(
            kind === "cancel" ? { ...target, intent: "cancel" } : target
        );
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
        } else if (action === "ended") {
            // The job finished before the cancel was recorded; it keeps its outcome.
            setNotice({
                type: "info",
                text: `${job.archiveFileName} finished before it could be cancelled.`,
            });
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
            cell: (job) => (
                <SpaceBetween size="xxs">
                    <span>{job.archiveFileName}</span>
                    <Box variant="small" color="text-body-secondary">
                        {`Request ID: ${job.requestId}`}
                    </Box>
                </SpaceBetween>
            ),
        },
        ...(showAllUsers
            ? [
                  {
                      id: "requestedBy",
                      header: "Requested by",
                      cell: (job: ZipDownloadJob) =>
                          job.isOwn ? `${job.requestedBy || ""} (you)` : job.requestedBy || "",
                  },
              ]
            : []),
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
                // All-users view: each control follows the API's own decision for this row (the
                // canDownload / canRemove flags it returns); another user's job is never cancelled
                // or deleted here. The per-user views keep the route decisions above.
                const shared = showAllUsers;
                const offerDownload = job.status === "SUCCEEDED" && (!shared || !!job.canDownload);
                const offerRemove = !shared || !!job.canRemove;
                return (
                    <SpaceBetween direction="horizontal" size="xs">
                        {offerDownload && (
                            <Button
                                iconName="download"
                                onClick={() => download(job)}
                                loading={busyId === job.requestId}
                                disabled={!shared && !!downloadReason}
                                disabledReason={shared ? undefined : downloadReason}
                                ariaLabel={`Download ${job.archiveFileName}`}
                            >
                                Download
                            </Button>
                        )}
                        {offerRemove && (
                            <Button
                                onClick={() =>
                                    setConfirm({ job, kind: terminal ? "delete" : "cancel" })
                                }
                                disabled={!shared && !!removeReason}
                                disabledReason={shared ? undefined : removeReason}
                                ariaLabel={`${terminal ? "Delete" : "Cancel"} ${
                                    job.archiveFileName
                                }`}
                            >
                                {terminal ? "Delete" : "Cancel"}
                            </Button>
                        )}
                    </SpaceBetween>
                );
            },
        },
    ];

    if (!permissionsLoading && !permissionsFailed && !canList) {
        return (
            <Alert
                type="info"
                statusIconAriaLabel="Info"
                header="The Download Queue is not available"
            >
                Your role does not allow listing download jobs.
            </Alert>
        );
    }

    if (disabled) {
        return (
            <Alert
                type="info"
                statusIconAriaLabel="Info"
                header="The Download Queue is not available"
            >
                {ZIP_DISABLED_MESSAGE}.
            </Alert>
        );
    }

    // The search filters the rows loaded so far; Load more adds older jobs, which the search then
    // also filters. The list is paged by the API (newest first), so nothing is searched server-side.
    const shownJobs = filterZipJobs(jobs, filterText);
    const filtering = filterText.trim() !== "";
    const more = nextToken ? "+" : "";

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
                    header="The Download Queue could not be loaded"
                    action={<Button onClick={() => load(false)}>Retry</Button>}
                >
                    {loadError}
                </Alert>
            )}
            <Table
                trackBy="requestId"
                items={shownJobs}
                columnDefinitions={columns}
                loading={loading || permissionsLoading || (!loaded && !loadError && isActive)}
                loadingText="Loading download jobs"
                wrapLines
                variant={assetScoped ? "embedded" : "container"}
                filter={
                    <div
                        style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: "8px",
                            flexWrap: "wrap",
                        }}
                    >
                        {canListAllUsers && (
                            <SegmentedControl
                                selectedId={scope}
                                label="Whose download jobs to show"
                                onChange={({ detail }) => {
                                    setScope(detail.selectedId === "all" ? "all" : "mine");
                                    setLoaded(false);
                                    setJobs([]);
                                    setNextToken(null);
                                }}
                                options={[
                                    { id: "mine", text: "My jobs" },
                                    { id: "all", text: "Show all users" },
                                ]}
                            />
                        )}
                        <TextFilter
                            filteringText={filterText}
                            filteringPlaceholder="Find by asset ID, request ID or archive name"
                            filteringAriaLabel="Search download jobs"
                            filteringClearAriaLabel="Clear search text"
                            onChange={({ detail }) => setFilterText(detail.filteringText)}
                            countText={
                                filtering
                                    ? `${shownJobs.length} ${
                                          shownJobs.length === 1 ? "match" : "matches"
                                      }`
                                    : undefined
                            }
                        />
                        <Button
                            iconName="refresh"
                            variant="icon"
                            ariaLabel="Refresh download queue"
                            onClick={() => load(false)}
                            loading={loading}
                        />
                    </div>
                }
                header={
                    <Header
                        variant={headerVariant}
                        counter={
                            !loaded
                                ? undefined
                                : filtering
                                ? `(${shownJobs.length}/${jobs.length}${more})`
                                : `(${jobs.length}${more})`
                        }
                        description={
                            (showAllUsers
                                ? `Archive jobs every user requested for this ${Synonyms.asset}. Only the requester can cancel or delete a job.`
                                : assetScoped
                                ? `Archive jobs you requested for this ${Synonyms.asset}.`
                                : `Archive jobs you requested across all ${Synonyms.assets}.`) +
                            (nextToken
                                ? " The search covers the jobs loaded so far; Load more adds older jobs."
                                : "")
                        }
                    >
                        Download jobs
                    </Header>
                }
                empty={
                    filtering && jobs.length > 0 ? (
                        <Box textAlign="center" color="inherit" padding="m">
                            <b>No matching download jobs</b>
                            <Box variant="p" color="inherit">
                                {`No loaded job matches "${filterText.trim()}".`}
                            </Box>
                            <Button onClick={() => setFilterText("")}>Clear search</Button>
                        </Box>
                    ) : (
                        <Box textAlign="center" color="inherit" padding="m">
                            <b>No download jobs</b>
                            <Box variant="p" color="inherit">
                                {`Select files or folders in the File Manager and choose Export, then Download as Zip.`}
                            </Box>
                        </Box>
                    )
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
                header={confirm?.kind === "cancel" ? "Cancel archive job" : "Delete archive job"}
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                variant="link"
                                onClick={() => setConfirm(null)}
                                disabled={confirming}
                            >
                                {finishedWhileOpen
                                    ? "Close"
                                    : confirm?.kind === "cancel"
                                    ? "Keep it"
                                    : "Keep"}
                            </Button>
                            <Button
                                variant="primary"
                                onClick={runConfirm}
                                loading={confirming}
                                disabled={finishedWhileOpen}
                            >
                                {confirm?.kind === "cancel" ? "Cancel download" : "Delete"}
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                {confirm?.kind === "cancel" ? (
                    <SpaceBetween size="s">
                        {!finishedWhileOpen && (
                            <Box variant="p">
                                {`Stop preparing ${confirm.job.archiveFileName}? The partial archive is discarded.`}
                            </Box>
                        )}
                        {/* Announced when the job finishes while the dialog is open. */}
                        <div role="status" aria-live="polite">
                            {finishedWhileOpen && confirmJob && (
                                <Alert
                                    type="info"
                                    statusIconAriaLabel="Info"
                                    header="This archive job has finished"
                                >
                                    {`${confirmJob.archiveFileName} finished (${
                                        STATUS_LABELS[confirmJob.status] ?? confirmJob.status
                                    }) before it was cancelled. Nothing was cancelled or deleted.`}
                                </Alert>
                            )}
                        </div>
                    </SpaceBetween>
                ) : confirm ? (
                    `Delete ${confirm.job.archiveFileName}? The archive is removed and cannot be recovered.`
                ) : null}
            </Modal>
        </SpaceBetween>
    );
};

export default ZipDownloadsTable;
