/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useCallback } from 'react';
import { ToastNotification } from '../types';

/**
 * Custom hook for managing toast notifications
 */
export const useToasts = () => {
    const [toasts, setToasts] = useState<ToastNotification[]>([]);

    const addToast = useCallback((
        type: ToastNotification['type'],
        title: string,
        message?: string,
        options?: {
            dismissible?: boolean;
            autoHide?: boolean;
            duration?: number;
        }
    ) => {
        const id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const toast: ToastNotification = {
            id,
            type,
            title,
            message,
            dismissible: options?.dismissible ?? true,
            autoHide: options?.autoHide ?? true,
            duration: options?.duration ?? (type === 'error' ? 8000 : 5000),
        };

        setToasts(prev => [...prev, toast]);

        // Auto-hide toast if enabled
        if (toast.autoHide) {
            setTimeout(() => {
                removeToast(id);
            }, toast.duration);
        }

        return id;
    }, []);

    const removeToast = useCallback((id: string) => {
        setToasts(prev => prev.filter(toast => toast.id !== id));
    }, []);

    const clearAllToasts = useCallback(() => {
        setToasts([]);
    }, []);

    // Convenience methods for different toast types
    const showSuccess = useCallback((title: string, message?: string, options?: any) => {
        return addToast('success', title, message, options);
    }, [addToast]);

    const showError = useCallback((title: string, message?: string, options?: any) => {
        return addToast('error', title, message, options);
    }, [addToast]);

    const showWarning = useCallback((title: string, message?: string, options?: any) => {
        return addToast('warning', title, message, options);
    }, [addToast]);

    const showInfo = useCallback((title: string, message?: string, options?: any) => {
        return addToast('info', title, message, options);
    }, [addToast]);

    return {
        toasts,
        addToast,
        removeToast,
        clearAllToasts,
        showSuccess,
        showError,
        showWarning,
        showInfo,
    };
};

export default useToasts;
