/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
// Subpath import per web/CLAUDE.md Rule 2 — the barrel export pulls in the whole library.
import Flashbar, { FlashbarProps } from "@cloudscape-design/components/flashbar";
import { ToastNotification } from "../../../components/search/types";

/**
 * Toast notifications for the orchestration module.
 *
 * These render through the same **Cloudscape `Flashbar`** the rest of the application notifies with
 * (`components/search/SearchNotifications/ToastManager.tsx`), in the same fixed top-right position, so
 * a pipeline/workflow/execution notification is visually and behaviorally indistinguishable from a
 * search or metadata one. The module's Cloudscape-free rule governs *page content* — this is a global
 * overlay mounted from `App.tsx`, which already renders Cloudscape (`TopNavigation`), and Tailwind's
 * preflight is disabled, so there is no style bleed in either direction.
 *
 * The shared `ToastNotification` shape from `components/search/types` is reused deliberately: one
 * notification contract for the whole app rather than a second parallel one.
 *
 * What this adds over the search hook is lifecycle safety and mutation ergonomics: timers are cleared
 * on unmount, identical repeats collapse instead of stacking, the stack is capped, and every mutation
 * surface reports through `useToast()` so a rejected request is always visible and a successful one is
 * always confirmed.
 */

export type ToastVariant = ToastNotification["type"];

export interface ToastOptions {
    /** Detail line under the header — typically the backend's message. */
    description?: string;
    /** Overrides the per-variant default. 0 pins the toast open until dismissed. */
    duration?: number;
}

interface ToastContextValue {
    toasts: ToastNotification[];
    /** Raise a toast and return its id (so a caller can dismiss its own). */
    notify: (variant: ToastVariant, title: string, options?: ToastOptions) => string;
    error: (title: string, options?: ToastOptions) => string;
    success: (title: string, options?: ToastOptions) => string;
    warning: (title: string, options?: ToastOptions) => string;
    info: (title: string, options?: ToastOptions) => string;
    dismiss: (id: string) => void;
    dismissAll: () => void;
}

/**
 * Matches the app's existing durations (`components/search/hooks/useToasts.tsx`): 8s for an error, 5s
 * for everything else. A caller that needs a failure to persist passes `duration: 0` explicitly rather
 * than this module having its own quietly different policy.
 */
const DEFAULT_DURATION: Record<ToastVariant, number> = {
    error: 8000,
    warning: 5000,
    success: 5000,
    info: 5000,
};

const ToastContext = React.createContext<ToastContextValue | null>(null);

/**
 * Access the toast API. Safe to call outside a provider: it degrades to a no-op that still logs, so a
 * component rendered in isolation (or in a unit test without the provider) never throws.
 */
export function useToast(): ToastContextValue {
    const ctx = React.useContext(ToastContext);
    if (ctx) return ctx;
    const fallback = (variant: ToastVariant, title: string, options?: ToastOptions) => {
        console.log(
            `[toast:${variant}] ${title}${options?.description ? ` — ${options.description}` : ""}`
        );
        return "";
    };
    return {
        toasts: [],
        notify: fallback,
        error: (t, o) => fallback("error", t, o),
        success: (t, o) => fallback("success", t, o),
        warning: (t, o) => fallback("warning", t, o),
        info: (t, o) => fallback("info", t, o),
        dismiss: () => undefined,
        dismissAll: () => undefined,
    };
}

let counter = 0;
const nextId = () => `toast-${++counter}`;

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [toasts, setToasts] = React.useState<ToastNotification[]>([]);
    // Timers are cleared on unmount so a dismissal never fires against an unmounted provider.
    const timers = React.useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

    const dismiss = React.useCallback((id: string) => {
        const timer = timers.current.get(id);
        if (timer) {
            clearTimeout(timer);
            timers.current.delete(id);
        }
        setToasts((prev) => prev.filter((t) => t.id !== id));
    }, []);

    const dismissAll = React.useCallback(() => {
        timers.current.forEach(clearTimeout);
        timers.current.clear();
        setToasts([]);
    }, []);

    const notify = React.useCallback(
        (variant: ToastVariant, title: string, options?: ToastOptions) => {
            const id = nextId();
            const duration = options?.duration ?? DEFAULT_DURATION[variant];
            setToasts((prev) => {
                // Repeating an identical message (e.g. a retried mutation failing the same way) should
                // not stack duplicates.
                const duplicate = prev.find(
                    (t) =>
                        t.type === variant &&
                        t.title === title &&
                        t.message === options?.description
                );
                if (duplicate) return prev;
                const next: ToastNotification[] = [
                    ...prev,
                    {
                        id,
                        type: variant,
                        title,
                        message: options?.description,
                        dismissible: true,
                        autoHide: duration > 0,
                        duration,
                    },
                ];
                // Cap the stack so a burst (a group abort over many executions) cannot fill the
                // viewport; the oldest are dropped first.
                return next.length > 4 ? next.slice(next.length - 4) : next;
            });
            if (duration > 0) {
                timers.current.set(
                    id,
                    setTimeout(() => dismiss(id), duration)
                );
            }
            return id;
        },
        [dismiss]
    );

    React.useEffect(() => {
        const map = timers.current;
        return () => {
            map.forEach(clearTimeout);
            map.clear();
        };
    }, []);

    const value = React.useMemo<ToastContextValue>(
        () => ({
            toasts,
            notify,
            error: (title, options) => notify("error", title, options),
            success: (title, options) => notify("success", title, options),
            warning: (title, options) => notify("warning", title, options),
            info: (title, options) => notify("info", title, options),
            dismiss,
            dismissAll,
        }),
        [toasts, notify, dismiss, dismissAll]
    );

    return (
        <ToastContext.Provider value={value}>
            {children}
            <ToastViewport toasts={toasts} onDismiss={dismiss} />
        </ToastContext.Provider>
    );
};

/**
 * Fixed top-right stack, mirroring `SearchNotifications/ToastManager.tsx` so both notification sources
 * appear in the same place with the same Cloudscape styling, icons, and dark-mode behavior.
 *
 * The z-index is the one deviation from that component (which uses 1000): the orchestration module's
 * dialogs and drawers sit at 3001 and the app's fixed TopNavigation at 2000, so a failure raised from
 * inside a modal would otherwise be painted underneath it.
 */
const ToastViewport: React.FC<{
    toasts: ToastNotification[];
    onDismiss: (id: string) => void;
}> = ({ toasts, onDismiss }) => {
    if (toasts.length === 0) return null;

    const items: FlashbarProps.MessageDefinition[] = toasts.map((toast) => ({
        id: toast.id,
        type: toast.type,
        header: toast.title,
        content: toast.message,
        dismissible: toast.dismissible,
        // Cloudscape renders the dismiss control without an accessible name unless one is supplied,
        // so screen-reader users would hear an unlabelled button.
        dismissLabel: `Dismiss ${toast.type} notification`,
        onDismiss: () => onDismiss(toast.id),
        // Cloudscape announces the item to assistive technology; an error is delivered assertively.
        ariaRole: toast.type === "error" ? "alert" : "status",
    }));

    return (
        <div
            aria-label="Notifications"
            style={{
                position: "fixed",
                top: "20px",
                right: "20px",
                zIndex: 4000,
                maxWidth: "400px",
            }}
        >
            <Flashbar items={items} />
        </div>
    );
};

/**
 * Normalize whatever a rejected mutation threw into a message worth showing. The orchestration
 * services reject with an Error carrying the backend's message; a raw string or an unexpected shape
 * still yields something better than "[object Object]".
 */
export function toastErrorMessage(err: unknown, fallback = "The request was rejected."): string {
    if (!err) return fallback;
    if (typeof err === "string") return err;
    if (err instanceof Error) return err.message || fallback;
    if (typeof err === "object") {
        const anyErr = err as Record<string, unknown>;
        for (const key of ["message", "error", "detail"]) {
            const v = anyErr[key];
            if (typeof v === "string" && v) return v;
        }
    }
    return fallback;
}

export default ToastProvider;
