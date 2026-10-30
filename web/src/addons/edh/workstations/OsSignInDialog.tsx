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
import { useOsSignIn } from "../api/queries";
import { osFamilyLabel } from "../labels";
import type { EdhOsFamily, OsSignIn } from "../types";

interface OsSignInDialogProps {
    sessionId: string;
    workstationName: string;
    /** The row's OS family, which titles the dialog before the sign-in answers. */
    osFamily: EdhOsFamily;
    onClose: () => void;
}

const MASK = "••••••••••••";

const linkButton =
    "bg-transparent border-0 p-0 text-sm text-blue-600 dark:text-blue-400 hover:underline cursor-pointer";

/** The steps for each OS family and credential; the service sends none of its own. */
const SignInSteps: React.FC<{ signIn: OsSignIn }> = ({ signIn }) => {
    if (signIn.credential === "none") {
        return (
            <ol className="list-decimal pl-5 text-sm space-y-1">
                <li>
                    Choose Connect. The desktop opens in a new tab, already signed in: Amazon DCV
                    signs the session in.
                </li>
                <li>A desktop built from the current recipes does not lock its screen.</li>
            </ol>
        );
    }
    const secret =
        signIn.credential === "directoryPassword" ? "your directory password" : "the password";
    return (
        <ol className="list-decimal pl-5 text-sm space-y-1">
            <li>Connect opens the desktop in a new tab at the Windows sign-in screen.</li>
            <li>
                In the Amazon DCV toolbar, open the menu under the computer name and choose Send
                Ctrl + Alt + Del.
            </li>
            <li>
                Choose the user name above in the lower-left corner if Windows offers another
                account, then enter {secret}.
            </li>
            <li>
                The desktop opens at the sign-in screen again after each start. Once you are signed
                in, a desktop from the current image does not lock while idle or when the last
                connection closes.
            </li>
        </ol>
    );
};

/**
 * How to sign in to one of the caller's running workstations. Windows: the user name and the
 * password EDH generated for the desktop, or, for a directory account, the user name alone (its
 * owner signs in with their directory password). Linux: no password is needed, because Amazon DCV
 * signs the session in. The sign-in is requested when the dialog opens and held only in the
 * request's state, which is dropped when the dialog closes; a password stays masked until the user
 * reveals it and is never written to the URL or to browser storage.
 */
const OsSignInDialog: React.FC<OsSignInDialogProps> = ({
    sessionId,
    workstationName,
    osFamily,
    onClose,
}) => {
    const toast = useToast();
    // The latest toast API, read by the request's error callback without re-running the request.
    const toastRef = useRef(toast);
    useEffect(() => {
        toastRef.current = toast;
    });
    const { mutate, reset, data, error } = useOsSignIn();
    const [revealed, setRevealed] = useState(false);

    useEffect(() => {
        mutate(sessionId, {
            onError: (err) =>
                toastRef.current.error("Desktop sign-in unavailable", {
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
    const password = data?.credential === "desktopPassword" ? data.password : null;

    return (
        <Dialog
            open
            onOpenChange={(open) => {
                if (!open) onClose();
            }}
            title={`${osFamilyLabel(data?.osFamily ?? osFamily)} sign-in for ${workstationName}`}
            footer={
                <button type="button" onClick={onClose} className={btnSecondary}>
                    Close
                </button>
            }
        >
            {loading && <p role="status">Fetching the desktop sign-in…</p>}
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
                            {data.credential === "none" && (
                                <span className="text-text-secondary">No password is needed.</span>
                            )}
                            {data.credential === "directoryPassword" && (
                                <span className="text-text-secondary">
                                    Sign in with your directory password.
                                </span>
                            )}
                            {password !== null && (
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
                    <SignInSteps signIn={data} />
                </div>
            )}
        </Dialog>
    );
};

export default OsSignInDialog;
