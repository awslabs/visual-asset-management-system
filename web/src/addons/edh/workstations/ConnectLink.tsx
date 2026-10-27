/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useId } from "react";
import { useWorkstationConnect } from "../api/queries";
import {
    useToast,
    toastErrorMessage,
} from "../../../features/orchestration/components/ToastProvider";
import type { WorkstationRecord } from "../types";
import { useInFlight } from "../components/useInFlight";

interface ConnectLinkProps {
    row: WorkstationRecord;
}

/** What a running workstation whose remote display session has ended shows under its inert Connect. */
export const DISPLAY_SESSION_ENDED_MESSAGE =
    "The workstation is running but its remote display session has ended. Stop it, then start it again, to reconnect.";

/** Why Connect is inert for a row that cannot be connected to yet, or any more. */
export function connectUnavailableReason(row: WorkstationRecord): string {
    if (row.connectionUnavailableReason === "DISPLAY_SESSION_ENDED") {
        return DISPLAY_SESSION_ENDED_MESSAGE;
    }
    switch (row.state) {
        case "ORPHANED":
            return "The launch did not complete.";
        case "STOPPED":
            return "Start the workstation to connect.";
        case "DELETED":
            return "The workstation has been deleted.";
        default:
            return "Available once the workstation is running.";
    }
}

/**
 * The Connect control for one row: a button styled as a link. The click opens a blank tab first —
 * inside the user gesture, so popup blockers allow it — then requests the connection URL and points
 * the tab at it; a failed request closes the tab and raises a toast. Nothing is fetched on render, so
 * no credential-bearing URL sits in the DOM before the user asks. The URL is EDH's own DCV entry
 * point, opened as a top-level navigation, so no CSP directive is involved. A row that cannot be
 * connected to renders the control inert, with the reason under it and as its description.
 */
const ConnectLink: React.FC<ConnectLinkProps> = ({ row }) => {
    const toast = useToast();
    const connect = useWorkstationConnect();
    const { claim, release } = useInFlight();
    const descriptionId = useId();
    // A running row whose display session looked ended stays clickable: the click asks EDH again, so a
    // brief broker gap clears itself, and a session that really ended answers with the same reason.
    const displayEnded = row.connectionUnavailableReason === "DISPLAY_SESSION_ENDED";
    const ready =
        row.state === "ACTIVE" && !!row.sessionId && (row.connectionAvailable || displayEnded);

    if (!ready) {
        return (
            <div>
                <button
                    type="button"
                    aria-disabled="true"
                    aria-describedby={descriptionId}
                    onClick={(e) => e.preventDefault()}
                    className="bg-transparent border-0 p-0 text-sm text-text-disabled cursor-not-allowed"
                >
                    Connect
                </button>
                <span id={descriptionId} className="block text-xs text-text-secondary">
                    {connectUnavailableReason(row)}
                </span>
            </div>
        );
    }

    const open = async () => {
        if (!row.sessionId || !claim("connect")) return;
        const tab = window.open("", "_blank");
        if (!tab) {
            release("connect");
            toast.error("Connect failed", {
                description: `${row.name}: the browser blocked the new tab. Allow pop-ups for this site and try again.`,
            });
            return;
        }
        // The desktop must not reach back into this window.
        tab.opener = null;
        try {
            const connection = await connect.mutateAsync(row.sessionId);
            tab.location.replace(connection.connectionUrl);
            // The URL is a desktop credential: drop it from the mutation state once the tab has it.
            connect.reset();
        } catch (err) {
            tab.close();
            toast.error("Connect failed", {
                description: `${row.name}: ${toastErrorMessage(err)}`,
            });
        } finally {
            release("connect");
        }
    };

    return (
        <>
            <button
                type="button"
                onClick={open}
                disabled={connect.isPending}
                aria-label={`Connect to ${row.name}`}
                aria-describedby={descriptionId}
                className="bg-transparent border-0 p-0 text-sm text-blue-600 dark:text-blue-400 hover:underline cursor-pointer disabled:cursor-progress disabled:no-underline"
            >
                {connect.isPending ? "Opening…" : "Connect"}
            </button>
            {displayEnded ? (
                <span id={descriptionId} className="block text-xs text-text-secondary">
                    {DISPLAY_SESSION_ENDED_MESSAGE}
                </span>
            ) : (
                <span id={descriptionId} className="sr-only">
                    Opens in a new tab.
                </span>
            )}
        </>
    );
};

export default ConnectLink;
