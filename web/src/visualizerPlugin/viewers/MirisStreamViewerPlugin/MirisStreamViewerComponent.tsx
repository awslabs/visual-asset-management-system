/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { ViewerPluginProps } from "../../core/types";
import { apiRefusalMessage } from "../../core/streamFailure";
import Synonyms from "../../../synonyms";
import { fetchFileInfo } from "../../../services/APIService";
import {
    ASSET_EXECUTIONS_API_ROUTE,
    EXECUTE_WORKFLOW_API_ROUTE,
    findRunningMirisUpload,
    getMirisStreamStatus,
    getMirisUploadRun,
    mirisUploadRunOf,
    MIRIS_REQUEST_BLOCKED,
    mintMirisViewerKey,
    startMirisUpload,
} from "../../../services/addons/mirisAPIService";
import { useAllowedRoutes } from "../../../features/orchestration/permissions/useAllowedRoutes";
import { appCache } from "../../../services/appCache";
import {
    EXECUTION_DETAILS_API_ROUTE,
    executionDetailPath,
} from "../../../components/filemanager/utils/executionLinks";
import {
    acceptsMirisUpload,
    isKnownVersionId,
    isStaleStream,
    latestVersionIdFromFileInfo,
    streamDisposition,
    toStatusFilePath,
} from "./streamStatus";
import { MirisStreamDependencyManager } from "./dependencies";
import type { MirisStreamElement } from "./mirisElements";
import styles from "./MirisStreamViewer.module.css";
import { addonFeaturesEnabled } from "../../../addons/addonFeatures";

/**
 * True when the deployment's secure-config lists the feature switch of the Miris upload pipeline,
 * which "Stream with Miris" runs.
 */
function uploadPipelineDeployed(): boolean {
    return !!appCache
        .getItem("config")
        ?.featuresEnabled?.includes(addonFeaturesEnabled.MIRIS_UPLOAD);
}

type ViewerError =
    | { kind: "NOT_CONFIGURED" }
    | { kind: "STATUS_FETCH" }
    | { kind: "FORBIDDEN" }
    | { kind: "MINT_FAILED" }
    | { kind: "REFUSED"; message: string }
    | { kind: "REQUEST_BLOCKED"; message: string }
    | { kind: "INVALID_UUID" }
    | { kind: "NOT_BOUND"; databaseId: string; assetId: string }
    | { kind: "LOAD_FAILED" }
    | { kind: "STREAM_TIMEOUT"; uuid: string }
    | { kind: "DEVICE_UNSUPPORTED" }
    | { kind: "MIRIS_PROCESSING_ERROR" };

type ProcessingState = { state: string };

// The file records no Miris stream; `filePath` is its asset-relative path, which an upload names.
type NotStreamed = { filePath: string };

// "Stream with Miris" request from the not-yet-streamed state.
type UploadRequest =
    | { kind: "starting" }
    | { kind: "failed" | "skipped" | "unknown"; message: string };

// Lookup of an unfinished Miris upload run on the asset, made before the button is offered, or the
// run the button started.
type RunningUpload = { kind: "checking" } | { kind: "running"; executionId: string };

type StreamInfo = {
    uuid: string;
    displayName: string | null;
    sourceVersionId: string | null;
    stale: boolean | null;
};

// A scene is rendered only once its viewer key exists: the SDK does not retry a stream that
// started without one, so a new key mounts a new scene (`generation` is its React key).
type SceneConfig = {
    uuid: string;
    viewerKey: string;
    generation: number;
};

// Poll Miris status every 90s while an asset is still being prepared on the
// Miris side.
const PROCESSING_POLL_INTERVAL_MS = 90_000;

// Poll an unfinished upload run every 30s until it ends; a run followed only through the stream
// status is polled on the processing interval.
const UPLOAD_RUN_POLL_INTERVAL_MS = 30_000;

// Shown when the workflow skipped the launch (SKIPPED), e.g. while another run held its lock.
const UPLOAD_SKIPPED_MESSAGE = "The Miris upload was skipped because another upload was running.";

// A run its source cannot see (not in the execution list, or no stream recorded on the file) is
// followed for at most this long, longer than a run lasts with the default upload settings; its
// result is then reported as unknown and the button is offered again.
const UPLOAD_RESULT_LIMIT_MS = 90 * 60_000;
const UPLOAD_UNKNOWN_MESSAGE =
    "The Miris upload did not report a result within 90 minutes, so its result is unknown. You can start the upload again.";

// First-frame load includes the WebSocket handshake, viewer key validation and the initial
// fetch from the content CDN; 60s gives cold connections headroom while still failing fast on
// a genuinely unreachable asset. Auth and CSP failures fire no event, so this is their signal.
// The SDK reports first data from its animation-frame loop, which a hidden tab does not run, so
// the timeout runs only while the tab is visible.
const STREAM_LOAD_TIMEOUT_MS = 60_000;

// The device check awaits a WebGPU adapter request and the SDK load awaits chunk fetches, any of
// which can stall on a broken GPU process or a stalled connection without ever rejecting; 60s
// covers a cold fetch of the SDK bundle on a slow link.
const SDK_LOAD_TIMEOUT_MS = 60_000;

function errorMessage(err: ViewerError): string {
    switch (err.kind) {
        case "NOT_CONFIGURED":
            return "Miris streaming is not configured for this deployment. Contact your administrator.";
        case "STATUS_FETCH":
            return "Could not load the Miris stream status for this file. Try again.";
        case "FORBIDDEN":
            return "You do not have permission to stream this asset.";
        case "MINT_FAILED":
            return "Could not get access to stream this asset from Miris. Try again.";
        case "REQUEST_BLOCKED":
        case "REFUSED":
            return err.message;
        case "INVALID_UUID":
            return "This file's miris_asset_uuid metadata is not a valid UUID. Correct it in the file's metadata, or run the Miris Spatial Streaming Upload workflow on it again.";
        case "NOT_BOUND":
            return (
                "The Miris asset recorded for this file is not linked to this VAMS asset: it must " +
                `exist in the Miris workspace and carry the vams-asset-${err.assetId} and ` +
                `vams-database-${err.databaseId} tags. Run the Miris Spatial Streaming Upload workflow on this file again, ` +
                "or add those tags to the Miris asset."
            );
        case "LOAD_FAILED":
            return "The Miris viewer failed to load. Try again.";
        case "STREAM_TIMEOUT":
            return (
                `Timed out waiting for Miris to start streaming asset ${err.uuid}. ` +
                "The asset reached a streamable state but the first frame didn't arrive in time. " +
                "Try again; if it keeps timing out, verify the asset still exists in app.miris.com."
            );
        case "DEVICE_UNSUPPORTED":
            return (
                "Miris streaming needs WebGPU, which this browser does not provide. Use a browser " +
                "with WebGPU, for example Chrome or Edge 113 or later, or Safari 26 or later."
            );
        case "MIRIS_PROCESSING_ERROR":
            return "Miris processing failed. Check this asset in app.miris.com.";
    }
}

/**
 * Viewer for USD source files (.usd/.usda/.usdc/.usdz) that streams the Miris asset recorded
 * in the file's `miris_*` metadata. The stream status endpoint resolves that metadata server
 * side, so the viewer sends only the file path:
 *   - notStreamed  -> "Not yet streamed" guidance, with a "Stream with Miris" button that starts
 *                     the upload workflow when the deployment includes the Miris upload pipeline
 *                     (MIRIS_UPLOAD) and the user is allowed to run workflows. While an upload run
 *                     on the asset has not finished, an in-progress notice replaces the button and
 *                     the run is followed: on success the status is read again, and on failure its
 *                     reason is shown with the button
 *   - invalidUuid  -> error
 *   - notBound     -> error: the Miris asset is not tagged for this VAMS asset
 *   - Miris still preparing the asset -> processing overlay, polled until it is streamable
 *   - otherwise    -> stream the returned UUID, with a stale banner when the file or a file it
 *                     uses has changed since the stream was built
 */
const MirisStreamViewerComponent: React.FC<ViewerPluginProps> = ({
    assetId,
    databaseId,
    assetKey,
    versionId,
    assetVersionId,
}) => {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<ViewerError | null>(null);
    const [notStreamed, setNotStreamed] = useState<NotStreamed | null>(null);
    const [uploadRequest, setUploadRequest] = useState<UploadRequest | null>(null);
    const [runningUpload, setRunningUpload] = useState<RunningUpload | null>(null);
    const [streamInfo, setStreamInfo] = useState<StreamInfo | null>(null);
    const [staleStream, setStaleStream] = useState(false);
    const [processingState, setProcessingState] = useState<ProcessingState | null>(null);
    // Viewer key and asset of the rendered scene, held in memory for this open only.
    const [scene, setScene] = useState<SceneConfig | null>(null);
    // Bumped to re-run load(): by the status poll when the state changes, and by Retry.
    const [reloadCount, setReloadCount] = useState(0);

    const streamRef = useRef<MirisStreamElement>(null);
    const sceneGenerationRef = useRef(0);
    const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
    // Bumped by every load, so an upload request that resolves after the viewed file changed is dropped.
    const loadGenerationRef = useRef(0);

    const { can } = useAllowedRoutes();
    const canExecuteWorkflow = can("POST", EXECUTE_WORKFLOW_API_ROUTE);
    const canViewExecution = can("GET", EXECUTION_DETAILS_API_ROUTE);
    const canListExecutions = can("GET", ASSET_EXECUTIONS_API_ROUTE);

    useEffect(() => {
        let cancelled = false;
        let stepTimer: ReturnType<typeof setTimeout> | undefined;
        loadGenerationRef.current += 1;

        // A step's result, or "timeout" when it has not settled within SDK_LOAD_TIMEOUT_MS. A step
        // that rejects after losing the race must not surface as unhandled.
        function bounded<T>(step: Promise<T>): Promise<T | "timeout"> {
            step.catch(() => undefined);
            const timeout = new Promise<"timeout">((resolve) => {
                stepTimer = setTimeout(() => resolve("timeout"), SDK_LOAD_TIMEOUT_MS);
            });
            return Promise.race([step, timeout]).finally(() => clearTimeout(stepTimer));
        }

        async function load() {
            try {
                if (!assetKey) {
                    setStreamInfo(null);
                    setScene(null);
                    setError(null);
                    setProcessingState(null);
                    setNotStreamed(null);
                    setUploadRequest(null);
                    setLoading(false);
                    return;
                }
                setLoading(true);
                setError(null);
                setNotStreamed(null);
                setUploadRequest(null);
                setScene(null);

                // 1. Stream status of the viewed file
                const filePath = toStatusFilePath(assetKey);
                const statusResult = await getMirisStreamStatus({ databaseId, assetId, filePath });
                if (cancelled) return;
                if (statusResult[0] !== true) {
                    const [, message, fetchStatus, fetchState] = statusResult;
                    if (fetchState === MIRIS_REQUEST_BLOCKED) {
                        setError({ kind: "REQUEST_BLOCKED", message });
                    } else if (fetchStatus === 403) {
                        setError({ kind: "FORBIDDEN" });
                    } else {
                        setError({ kind: "STATUS_FETCH" });
                    }
                    return;
                }
                const status = statusResult[1];

                const disposition = streamDisposition(status.state);
                if (disposition === "notStreamed") {
                    setStreamInfo(null);
                    setProcessingState(null);
                    setNotStreamed({ filePath: status.filePath });
                    setLoading(false);
                    return;
                }
                if (disposition === "invalidUuid") {
                    setError({ kind: "INVALID_UUID" });
                    return;
                }
                if (disposition === "notBound") {
                    setError({ kind: "NOT_BOUND", databaseId, assetId });
                    return;
                }
                if (disposition === "failed") {
                    setError({ kind: "MIRIS_PROCESSING_ERROR" });
                    return;
                }
                if (!status.mirisAssetUuid) {
                    setError({ kind: "STATUS_FETCH" });
                    return;
                }
                const uuid = status.mirisAssetUuid;
                setStreamInfo({
                    uuid,
                    displayName: status.displayName,
                    sourceVersionId: status.sourceVersionId,
                    stale: typeof status.stale === "boolean" ? status.stale : null,
                });

                // 2. Miris is still preparing the asset: overlay + poll. Any other
                // disposition re-runs load(), which handles it from the top.
                if (disposition === "processing") {
                    setProcessingState({ state: status.state });
                    setLoading(false);
                    if (pollIntervalRef.current) {
                        clearInterval(pollIntervalRef.current);
                    }
                    pollIntervalRef.current = setInterval(async () => {
                        try {
                            const r = await getMirisStreamStatus({
                                databaseId,
                                assetId,
                                filePath,
                            });
                            if (cancelled || r[0] !== true) return;
                            if (streamDisposition(r[1].state) === "processing") {
                                setProcessingState({ state: r[1].state });
                                return;
                            }
                            if (pollIntervalRef.current) {
                                clearInterval(pollIntervalRef.current);
                                pollIntervalRef.current = null;
                            }
                            setProcessingState(null);
                            setReloadCount((c) => c + 1);
                        } catch (_pollErr) {
                            // Swallow transient poll errors; the next tick will retry.
                        }
                    }, PROCESSING_POLL_INTERVAL_MS);
                    return;
                }
                setProcessingState(null);

                // 3. Device check, before a viewer key is requested
                const supported = await bounded(MirisStreamDependencyManager.isDeviceSupported());
                if (cancelled) return;
                if (supported !== true) {
                    setError({ kind: "DEVICE_UNSUPPORTED" });
                    return;
                }

                // 4. Viewer key minted for this file's Miris asset. Held in memory for this open
                // only.
                const keyResult = await mintMirisViewerKey({ databaseId, assetId, filePath });
                if (cancelled) return;
                if (keyResult[0] !== true) {
                    const [, message, keyStatus, keyState] = keyResult;
                    // The asset's distribution rule (not distributable, or quarantined), in the
                    // API's own words.
                    const refusal = apiRefusalMessage(keyStatus, message);
                    if (keyState === MIRIS_REQUEST_BLOCKED) {
                        setError({ kind: "REQUEST_BLOCKED", message });
                    } else if (keyStatus === 404) {
                        setStreamInfo(null);
                        setNotStreamed({ filePath: status.filePath });
                        setLoading(false);
                    } else if (keyStatus === 403) {
                        setError(
                            streamDisposition(keyState ?? "") === "notBound"
                                ? { kind: "NOT_BOUND", databaseId, assetId }
                                : { kind: "FORBIDDEN" }
                        );
                    } else if (keyStatus === 503) {
                        setError({ kind: "NOT_CONFIGURED" });
                    } else if (refusal) {
                        setError({ kind: "REFUSED", message: refusal });
                    } else {
                        setError({ kind: "MINT_FAILED" });
                    }
                    return;
                }
                if (keyResult[1].mirisAssetUuid !== uuid) {
                    setError({ kind: "MINT_FAILED" });
                    return;
                }
                const viewerKey = keyResult[1].viewerKey;

                // 5. SDK custom elements, then the scene with this key
                const outcome = await bounded(MirisStreamDependencyManager.loadMirisStream());
                if (cancelled) return;
                if (outcome === "timeout") {
                    MirisStreamDependencyManager.abandonPendingLoad();
                    setError({ kind: "LOAD_FAILED" });
                    return;
                }
                sceneGenerationRef.current += 1;
                setScene({ uuid, viewerKey, generation: sceneGenerationRef.current });
                setLoading(false);
            } catch (e) {
                // Diagnostic logging: do NOT pass the viewer key here.
                console.error("Miris viewer load failed", {
                    assetId,
                    databaseId,
                    assetKey,
                    error: e,
                });
                if (!cancelled) setError({ kind: "LOAD_FAILED" });
            }
        }

        void load();

        return () => {
            cancelled = true;
            clearTimeout(stepTimer);
            if (pollIntervalRef.current) {
                clearInterval(pollIntervalRef.current);
                pollIntervalRef.current = null;
            }
        };
    }, [assetId, databaseId, assetKey, reloadCount]);

    // Before the button is offered, the asset's executions are checked for an unfinished upload run,
    // which would otherwise be started a second time. A failed or skipped lookup offers the button.
    const notStreamedPath = notStreamed?.filePath ?? null;
    const canStartUpload =
        canExecuteWorkflow &&
        notStreamedPath !== null &&
        acceptsMirisUpload(notStreamedPath) &&
        uploadPipelineDeployed();
    const checkRunningUpload = canStartUpload && canListExecutions;
    useEffect(() => {
        setRunningUpload(null);
        if (!checkRunningUpload) return;
        let cancelled = false;
        setRunningUpload({ kind: "checking" });
        findRunningMirisUpload({ databaseId, assetId })
            .then((result) => {
                if (cancelled) return;
                setRunningUpload(
                    result[0] === true && result[1]
                        ? { kind: "running", executionId: result[1].workflowExecutionId }
                        : null
                );
            })
            .catch(() => {
                if (!cancelled) setRunningUpload(null);
            });
        return () => {
            cancelled = true;
        };
    }, [checkRunningUpload, notStreamedPath, assetId, databaseId]);

    // An unfinished run is followed until it ends: from its execution details when the user may read
    // them, else from the asset's execution list, else from the file's stream status. A source whose
    // read fails twice in a row gives way to the next. A run that succeeded re-runs load(); one that
    // failed shows its reason and offers the button again, and one that was skipped says so. A run
    // the source still cannot see after UPLOAD_RESULT_LIMIT_MS is reported as unknown.
    const followedExecutionId =
        runningUpload?.kind === "running" ? runningUpload.executionId : null;
    useEffect(() => {
        if (!followedExecutionId || !assetKey) return;
        const executionId = followedExecutionId;
        const filePath = toStatusFilePath(assetKey);
        const sources: Array<"details" | "list" | "status"> = [
            ...(canViewExecution ? (["details"] as const) : []),
            ...(canListExecutions ? (["list"] as const) : []),
            "status",
        ];
        const followedSince = Date.now();
        let failedReads = 0;
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout> | undefined;

        // The ended run (`status` is null when the source does not report one), "running" while the
        // source shows it unfinished, "unseen" while the source cannot see it (not listed, or no
        // stream recorded yet), or null when the read failed.
        async function readRun(): Promise<
            { status: string | null; failure: string | null } | "running" | "unseen" | null
        > {
            if (sources[0] === "details") {
                const run = await getMirisUploadRun(executionId);
                if (run[0] !== true) return null;
                return run[1].finished ? run[1] : "running";
            }
            if (sources[0] === "list") {
                const listed = await findRunningMirisUpload({ databaseId, assetId, executionId });
                if (listed[0] !== true) return null;
                if (!listed[1]) return "unseen";
                const run = mirisUploadRunOf(listed[1]);
                return run.finished ? run : "running";
            }
            const r = await getMirisStreamStatus({ databaseId, assetId, filePath });
            if (r[0] !== true) return null;
            const recorded = streamDisposition(r[1].state) !== "notStreamed";
            return recorded ? { status: null, failure: null } : "unseen";
        }

        function schedule() {
            const interval =
                sources[0] === "status" ? PROCESSING_POLL_INTERVAL_MS : UPLOAD_RUN_POLL_INTERVAL_MS;
            timer = setTimeout(() => void tick(), interval);
        }

        async function tick() {
            const ended = await readRun().catch(() => null);
            if (cancelled) return;
            failedReads = ended === null ? failedReads + 1 : 0;
            if (failedReads >= 2 && sources.length > 1) {
                sources.shift();
                failedReads = 0;
            }
            if (ended === "unseen" && Date.now() - followedSince >= UPLOAD_RESULT_LIMIT_MS) {
                setRunningUpload(null);
                setUploadRequest({ kind: "unknown", message: UPLOAD_UNKNOWN_MESSAGE });
                return;
            }
            if (ended === null || ended === "running" || ended === "unseen") {
                schedule();
                return;
            }
            setRunningUpload(null);
            if (ended.status === "SKIPPED") {
                setUploadRequest({ kind: "skipped", message: UPLOAD_SKIPPED_MESSAGE });
            } else if (ended.failure !== null) {
                setUploadRequest({
                    kind: "failed",
                    message: `The Miris upload failed: ${ended.failure}`,
                });
            } else {
                setReloadCount((c) => c + 1);
            }
        }

        schedule();
        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [followedExecutionId, canViewExecution, canListExecutions, assetId, databaseId, assetKey]);

    // Starts the Miris Spatial Streaming Upload workflow on the viewed file.
    async function startUpload() {
        if (!notStreamed || uploadRequest?.kind === "starting") return;
        const generation = loadGenerationRef.current;
        setUploadRequest({ kind: "starting" });
        const result = await startMirisUpload({
            databaseId,
            assetId,
            filePath: notStreamed.filePath,
        });
        if (generation !== loadGenerationRef.current) return;
        if (result[0] !== true) {
            const [, message, uploadStatus, uploadState] = result;
            setUploadRequest({
                kind: "failed",
                message:
                    uploadState !== MIRIS_REQUEST_BLOCKED && uploadStatus === 403
                        ? "You don't have permission to run the Miris upload workflow."
                        : message,
            });
            return;
        }
        setUploadRequest(null);
        const executionId = result[1].executionId;
        if (executionId) {
            setRunningUpload({ kind: "running", executionId });
        } else {
            // No run to follow: the status read and the running-upload lookup start over.
            setReloadCount((c) => c + 1);
        }
    }

    // The scene element owns rendering, camera framing, resize and teardown. The stream's first
    // data ends the load timeout, which pauses while the tab is hidden and starts again in full
    // when it is visible. Any viewer error unmounts the scene, which releases its renderer. The
    // listener attaches in the commit, ahead of anything the elements dispatch once their async
    // connect runs.
    const mountedScene = error ? null : scene;
    useLayoutEffect(() => {
        const streamElement = streamRef.current;
        if (!mountedScene || !streamElement) return;
        let loadTimer: ReturnType<typeof setTimeout> | undefined;
        const restartTimer = () => {
            clearTimeout(loadTimer);
            if (document.visibilityState === "hidden") return;
            loadTimer = setTimeout(
                () => setError({ kind: "STREAM_TIMEOUT", uuid: mountedScene.uuid }),
                STREAM_LOAD_TIMEOUT_MS
            );
        };
        const stopTimer = () => {
            clearTimeout(loadTimer);
            document.removeEventListener("visibilitychange", restartTimer);
        };
        restartTimer();
        document.addEventListener("visibilitychange", restartTimer);
        streamElement.addEventListener("streamloaded", stopTimer);
        return () => {
            stopTimer();
            streamElement.removeEventListener("streamloaded", stopTimer);
        };
    }, [mountedScene]);

    // Stale-stream check. The status reports `stale` from the stream's dependency manifest. Without
    // one (hand-written metadata), the stream's source version is compared with the viewed file's:
    // the host passes it as `versionId`, but it can be absent for the current version (e.g. a
    // basic-mode listing, or before the host has resolved it), so the file's current S3 version is
    // looked up instead. A historical asset version without a file version id is not compared.
    // Never blocks streaming.
    const sourceVersionId = streamInfo?.sourceVersionId;
    const manifestStale = streamInfo?.stale ?? null;
    useEffect(() => {
        let cancelled = false;
        setStaleStream(manifestStale === true);
        if (manifestStale !== null || !isKnownVersionId(sourceVersionId) || !assetKey) return;

        async function checkStale() {
            let viewedVersionId: string | undefined = isKnownVersionId(versionId)
                ? versionId
                : undefined;
            if (!viewedVersionId && !assetVersionId) {
                try {
                    const [ok, fileInfo] = await fetchFileInfo({
                        databaseId,
                        assetId,
                        fileKey: assetKey,
                        includeVersions: true,
                    });
                    if (ok) viewedVersionId = latestVersionIdFromFileInfo(fileInfo);
                } catch (_fileInfoErr) {
                    // Advisory only: without the current version there is nothing to compare.
                }
            }
            if (!cancelled) setStaleStream(isStaleStream(sourceVersionId, viewedVersionId));
        }

        void checkStale();
        return () => {
            cancelled = true;
        };
    }, [manifestStale, sourceVersionId, assetId, databaseId, assetKey, versionId, assetVersionId]);

    if (notStreamed && !error) {
        const starting = uploadRequest?.kind === "starting";
        const running = runningUpload?.kind === "running" ? runningUpload : null;
        const offerUpload = canStartUpload && !running;
        return (
            <div className={styles.guidance}>
                <div className={styles.guidanceContent}>
                    <SpaceBetween size="m">
                        <Box variant="h3">
                            {running ? "Uploading to Miris" : "Not yet streamed with Miris"}
                        </Box>
                        {!running && (
                            <Box variant="p" color="text-body-secondary">
                                This USD file has not been uploaded to Miris Spatial Streaming.
                            </Box>
                        )}
                        {running ? (
                            <Alert type="info" statusIconAriaLabel="Info">
                                <SpaceBetween size="xs">
                                    <div>A Miris upload for this {Synonyms.asset} is running.</div>
                                    <div>
                                        The viewer shows the result when the upload ends. Miris then
                                        prepares the asset. This can take a few hours.
                                    </div>
                                    {canViewExecution && (
                                        <Link
                                            external
                                            externalIconAriaLabel="(opens in a new tab)"
                                            href={`#${executionDetailPath(running.executionId)}`}
                                        >
                                            View execution
                                        </Link>
                                    )}
                                </SpaceBetween>
                            </Alert>
                        ) : (
                            <Box variant="p" color="text-body-secondary">
                                To stream it,{" "}
                                {offerUpload && (
                                    <>
                                        choose <strong>Stream with Miris</strong> below, or{" "}
                                    </>
                                )}
                                use the file manager&apos;s <strong>Automation</strong> action to
                                run the <strong>Miris Spatial Streaming Upload</strong> workflow on
                                it; once processing completes, you can stream it here.
                            </Box>
                        )}
                        {offerUpload && (
                            <Button
                                variant="primary"
                                loading={starting || runningUpload?.kind === "checking"}
                                onClick={() => void startUpload()}
                            >
                                Stream with Miris
                            </Button>
                        )}
                        {offerUpload && uploadRequest && uploadRequest.kind !== "starting" && (
                            <Alert
                                type={uploadRequest.kind === "failed" ? "error" : "info"}
                                statusIconAriaLabel={
                                    uploadRequest.kind === "failed" ? "Error" : "Info"
                                }
                                action={<Button onClick={() => void startUpload()}>Retry</Button>}
                            >
                                {uploadRequest.message}
                            </Alert>
                        )}
                    </SpaceBetween>
                </div>
            </div>
        );
    }

    const streaming = !!streamInfo && !error && !loading && !processingState;

    return (
        <div className={styles.container}>
            {mountedScene && (
                <miris-scene
                    key={mountedScene.generation}
                    viewer-key={mountedScene.viewerKey}
                    camera-fit="contain"
                >
                    <miris-camera controls />
                    <miris-stream ref={streamRef} uuid={mountedScene.uuid} />
                </miris-scene>
            )}
            {error && (
                <div className={styles.errorOverlay}>
                    <div>{errorMessage(error)}</div>
                    <div className={styles.overlayAction}>
                        <Button
                            onClick={() => {
                                setScene(null);
                                setError(null);
                                setReloadCount((c) => c + 1);
                            }}
                        >
                            Retry
                        </Button>
                    </div>
                </div>
            )}
            {processingState && !error && (
                <div className={styles.errorOverlay}>
                    <div>Miris is preparing this asset.</div>
                    <div className={styles.overlayNote}>This can take a few hours.</div>
                    <div className={styles.overlayNote}>
                        The viewer will refresh automatically when it&rsquo;s ready.
                    </div>
                    <div className={styles.overlayState}>
                        Current state: {processingState.state}
                    </div>
                </div>
            )}
            {loading && !error && !processingState && (
                <div className={styles.errorOverlay}>Loading…</div>
            )}
            {streaming && (streamInfo?.displayName || staleStream) && (
                <div className={styles.topOverlay}>
                    {streamInfo?.displayName && (
                        <div className={styles.label}>{streamInfo.displayName}</div>
                    )}
                    {staleStream && (
                        <Alert type="warning" statusIconAriaLabel="Warning">
                            This asset has changed since it was streamed: this file, or a sublayer
                            or texture it uses, differs from the one in the stream. Run the Miris
                            Spatial Streaming Upload workflow again to update it.
                        </Alert>
                    )}
                </div>
            )}
        </div>
    );
};

export default MirisStreamViewerComponent;
