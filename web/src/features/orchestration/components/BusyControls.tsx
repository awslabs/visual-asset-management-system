/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

/**
 * The look of a busy control: the dimmed, not-allowed look `controlStyles` gives a disabled one,
 * keyed on `aria-disabled` instead.
 */
export const BUSY_LOOK = "aria-disabled:opacity-50 aria-disabled:cursor-not-allowed";

const withBusyLook = (className: string | undefined, busyClassName: string) =>
    className ? `${className} ${busyClassName}` : busyClassName;

type BusyButtonProps = Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "aria-disabled"> & {
    /** Whether the request the button sent, or would send, is in flight. */
    busy: boolean;
    /** What the button reads while busy, naming the action under way ("Deleting..."). */
    busyLabel?: string;
    /** The busy look; `BUSY_LOOK` without it. */
    busyClassName?: string;
};

/**
 * A button whose request may be in flight. While `busy` it keeps focus: the browser moves focus
 * from a control that becomes `disabled` to the page body, so the button carries
 * `aria-disabled="true"` instead, ignores a click, Enter or Space (a submit button submits nothing,
 * whether clicked or pressed through Enter in a field), and reads `busyLabel`, so a screen reader
 * names the action under way. `disabled` stays for a button unavailable for another reason.
 */
export const BusyButton = React.forwardRef<HTMLButtonElement, BusyButtonProps>(
    (
        {
            busy,
            busyLabel,
            busyClassName = BUSY_LOOK,
            className,
            onClick,
            type = "button",
            children,
            ...rest
        },
        ref
    ) => (
        <button
            {...rest}
            ref={ref}
            type={type}
            aria-disabled={busy || undefined}
            onClick={(event) => {
                // Cancelling the click also keeps a submit button from submitting its form.
                if (busy) event.preventDefault();
                else onClick?.(event);
            }}
            className={withBusyLook(className, busyClassName)}
        >
            {busy && busyLabel ? busyLabel : children}
        </button>
    )
);
BusyButton.displayName = "BusyButton";

type BusyInputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "aria-disabled"> & {
    /** Whether the form the field belongs to is being sent. */
    busy: boolean;
};

/**
 * A field of a form that may be being sent. While `busy` it keeps focus and its value: it carries
 * `aria-disabled="true"` and `readOnly` rather than `disabled`, and ignores a change, a checkbox's
 * toggle included, which `readOnly` does not stop. `disabled` stays for a field unavailable for
 * another reason.
 */
export const BusyInput: React.FC<BusyInputProps> = ({
    busy,
    className,
    onChange,
    readOnly,
    ...rest
}) => (
    <input
        {...rest}
        aria-disabled={busy || undefined}
        readOnly={busy || readOnly}
        onChange={(event) => {
            if (!busy) onChange?.(event);
        }}
        className={withBusyLook(className, BUSY_LOOK)}
    />
);

/**
 * A dialog's `onOpenChange` that keeps it open while its request is in flight: Escape, the close
 * button and a click outside close it only once the request is answered. A dialog closed then would
 * return focus to a control the answer may still take off the page, and its handler, closing the
 * dialog as the answer arrives, would close a dialog opened since.
 */
export const openWhileBusy =
    (busy: boolean, onOpenChange: (open: boolean) => void) => (open: boolean) => {
        if (open || !busy) onOpenChange(open);
    };
