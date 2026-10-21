/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import Dialog from "../../../features/orchestration/components/Dialog";
import { btnSecondary } from "../../../features/orchestration/components/controlStyles";
import {
    useToast,
    toastErrorMessage,
} from "../../../features/orchestration/components/ToastProvider";
import { useWindowsSignIn } from "../api/queries";

interface WindowsSignInDialogProps {
    sessionId: string;
    workstationName: string;
    onClose: () => void;
}

const MASK = "••••••••••••";

const linkButton =
    "bg-transparent border-0 p-0 text-sm text-blue-600 dark:text-blue-400 hover:underline cursor-pointer";

/**
 * The Windows user name and password of one of the caller's running Windows workstations. The
 * sign-in is requested when the dialog opens and held only in the request's state, which is dropped
 * when the dialog closes; the password stays masked until the user reveals it and is never written
 * to the URL or to browser storage. A directory account shows no password: its owner signs in with
 * their directory password.
 */
const WindowsSignInDialog: React.FC<WindowsSignInDialogProps> = ({
    sessionId,
    workstationName,
    onClose,
}) => {
    const toast = useToast();
    // The latest toast API, read by the request's error callback without re-running the request.
    const toastRef = useRef(toast);
    useEffect(() => {
        toastRef.current = toast;
    });
    const { mutate, reset, data, error } = useWindowsSignIn();
    const [revealed, setRevealed] = useState(false);

    useEffect(() => {
        mutate(sessionId, {
            onError: (err) =>
                toastRef.current.error("Windows sign-in unavailable", {
                    description: toastErrorMessage(err),
                }),
        });
        return () => reset();
    }, [sessionId, mutate, reset]);

    const copy = async (label: string, value: string) => {
        try {
            if (!navigator.clipboard) throw new Error("clipboard unavailable");
            await navigator.clipboard.writeText(value);
            toast.success(`${label} copied`);
        } catch {
            toast.error(`${label} could not be copied`, {
                description: "The browser refused clipboard access.",
            });
        }
    };

    const loading = !data && !error;
    const password = data && !data.directoryAccount ? data.password : null;

    return (
        <Dialog
            open
            onOpenChange={(open) => {
                if (!open) onClose();
            }}
            title={`Windows sign-in for ${workstationName}`}
            footer={
                <button type="button" onClick={onClose} className={btnSecondary}>
                    Close
                </button>
            }
        >
            {loading && <p role="status">Fetching the Windows sign-in…</p>}
            {error && (
                <p role="alert" className="text-vams-error">
                    {toastErrorMessage(error)}
                </p>
            )}
            {data && (
                <div className="space-y-4">
                    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
                        <dt className="text-text-secondary">User name</dt>
                        <dd className="flex items-center gap-3">
                            <span className="font-mono text-text-primary">{data.username}</span>
                            <button
                                type="button"
                                className={linkButton}
                                onClick={() => copy("User name", data.username)}
                            >
                                Copy user name
                            </button>
                        </dd>
                        <dt className="text-text-secondary">Password</dt>
                        <dd className="flex items-center gap-3">
                            {password === null ? (
                                <span className="text-text-secondary">
                                    Sign in with your directory password.
                                </span>
                            ) : (
                                <>
                                    <span className="font-mono text-text-primary">
                                        {revealed ? password : MASK}
                                    </span>
                                    <button
                                        type="button"
                                        className={linkButton}
                                        onClick={() => setRevealed((shown) => !shown)}
                                    >
                                        {revealed ? "Hide password" : "Show password"}
                                    </button>
                                    <button
                                        type="button"
                                        className={linkButton}
                                        onClick={() => copy("Password", password)}
                                    >
                                        Copy password
                                    </button>
                                </>
                            )}
                        </dd>
                    </dl>
                    <ol className="list-decimal pl-5 text-sm space-y-1">
                        <li>Connect opens the desktop in a new tab at the Windows lock screen.</li>
                        <li>
                            In the Amazon DCV toolbar, open the menu under the computer name and
                            choose Send Ctrl + Alt + Del.
                        </li>
                        <li>
                            Choose the user name above in the lower-left corner if Windows offers
                            another account, then enter the password.
                        </li>
                        <li>
                            Amazon DCV locks Windows when the last connection closes; the same
                            password unlocks it.
                        </li>
                    </ol>
                </div>
            )}
        </Dialog>
    );
};

export default WindowsSignInDialog;
