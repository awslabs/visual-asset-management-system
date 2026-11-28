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
import { isLaunchStalled } from "../labels";
import { BusyButton } from "../../../features/orchestration/components/BusyControls";
import { useInFlight } from "../components/useInFlight";

interface ConnectLinkProps {
    row: WorkstationRecord;
}

/** What a running workstation whose remote display session has ended shows under its inert Connect. */
export const DISPLAY_SESSION_ENDED_MESSAGE =
    "The workstation is running but its remote display session has ended. Stop it, then start it again, to reconnect.";

/** What a workstation EDH reports in error shows under its Connect. */
export const DESKTOP_ERROR_MESSAGE =
    "The workstation's remote display is not responding; its disk and data are kept. Wait a few minutes, or stop it, then start it again, to reconnect.";

/**
 * The sentence the service's `connectionUnavailableReason` stands for, or null for none or an unknown
 * one. A launch the sweep found stalled (`isLaunchStalled`) never ran, so its own sentence and Delete
 * apply rather than a desktop error's.
 */
export function connectionUnavailableMessage(row: WorkstationRecord): string | null {
    if (isLaunchStalled(row)) {
        return null;
    }
    if (row.connectionUnavailableReason === "DISPLAY_SESSION_ENDED") {
        return DISPLAY_SESSION_ENDED_MESSAGE;
    }
    if (row.connectionUnavailableReason === "DESKTOP_ERROR") {
        return DESKTOP_ERROR_MESSAGE;
    }
    return null;
}

/** Why Connect is inert for a row that cannot be connected to yet, or any more. */
export function connectUnavailableReason(row: WorkstationRecord): string {
    const reason = connectionUnavailableMessage(row);
    if (reason) {
        return reason;
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

/** The connection link when it parses as an `https:` URL, else null. */
function httpsLink(value: unknown): string | null {
    if (typeof value !== "string") return null;
    try {
        return new URL(value).protocol === "https:" ? value : null;
    } catch {
        return null;
    }
}

/**
 * The Connect control for one row: a button styled as a link. The click opens a blank tab first —
 * inside the user gesture, so popup blockers allow it — then requests the connection URL and points
 * the tab at it when it is an `https:` link; a failed request, or any other link, closes the tab and
 * raises a toast. Nothing is fetched on render, so
 * no credential-bearing URL sits in the DOM before the user asks. The URL is EDH's own DCV entry
 * point, opened as a top-level navigation, so no CSP directive is involved. A row that cannot be
 * connected to renders the control inert, with the reason under it and as its description.
 */
const ConnectLink: React.FC<ConnectLinkProps> = ({ row }) => {
    const toast = useToast();
    const connect = useWorkstationConnect();
    const { claim, release } = useInFlight();
    const descriptionId = useId();
    // A running row whose display session looked ended, or that EDH reported in error, stays clickable:
    // the click asks EDH again, so a brief broker gap or a recovered desktop clears itself, and a desktop
    // still in that state answers with the same reason.
    const reasonMessage = row.state === "ACTIVE" ? connectionUnavailableMessage(row) : null;
    const ready =
        row.state === "ACTIVE" && !!row.sessionId && (row.connectionAvailable || !!reasonMessage);

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
            const link = httpsLink(connection.connectionUrl);
            // The URL is a desktop credential: drop it from the mutation state once it is read.
            connect.reset();
            if (!link) {
                tab.close();
                toast.error("Connect failed", {
                    description: `${row.name}: the cluster returned a connection link that cannot be opened.`,
                });
                return;
            }
            tab.location.replace(link);
        } catch (err) {
            tab.close();
            toast.error("Connect failed", {
                description: `${row.name}: ${toastErrorMessage(err)}`,
            });
        } finally {
            release("connect");
        }
    };

    // While the URL is requested the control keeps focus and says the desktop is being opened; its
    // name starts with its visible word, as "Connect to <name>" does.
    const opening = connect.isPending;
    return (
        <>
            <BusyButton
                onClick={open}
                busy={opening}
                busyLabel="Opening…"
                busyClassName="aria-disabled:cursor-progress aria-disabled:no-underline"
                aria-label={opening ? `Opening ${row.name}…` : `Connect to ${row.name}`}
                aria-describedby={descriptionId}
                className="bg-transparent border-0 p-0 text-sm text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
            >
                Connect
            </BusyButton>
            {reasonMessage ? (
                <span id={descriptionId} className="block text-xs text-text-secondary">
                    {reasonMessage}
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
