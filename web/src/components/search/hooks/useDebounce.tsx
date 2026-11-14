/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useRef, useCallback, useMemo, useState } from "react";

/** A debounced function plus `cancel`, which drops a pending call without invoking it. */
export type DebouncedFunction<T extends (...args: any[]) => any> = ((
    ...args: Parameters<T>
) => void) & { cancel: () => void };

/**
 * Custom hook for debouncing function calls
 * @param callback Function to debounce
 * @param delay Delay in milliseconds
 * @returns Debounced function with a `cancel` method
 */
export function useDebounce<T extends (...args: any[]) => any>(
    callback: T,
    delay: number
): DebouncedFunction<T> {
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);
    const callbackRef = useRef(callback);

    // Update callback ref when callback changes
    useEffect(() => {
        callbackRef.current = callback;
    }, [callback]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (timeoutRef.current) {
                clearTimeout(timeoutRef.current);
            }
        };
    }, []);

    const cancel = useCallback(() => {
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
            timeoutRef.current = null;
        }
    }, []);

    const debounced = useCallback(
        (...args: Parameters<T>) => {
            if (timeoutRef.current) {
                clearTimeout(timeoutRef.current);
            }

            timeoutRef.current = setTimeout(() => {
                timeoutRef.current = null;
                callbackRef.current(...args);
            }, delay);
        },
        [delay]
    );

    return useMemo(() => Object.assign(debounced, { cancel }), [debounced, cancel]);
}

/**
 * Custom hook for debouncing values
 * @param value Value to debounce
 * @param delay Delay in milliseconds
 * @returns Debounced value
 */
export function useDebouncedValue<T>(value: T, delay: number): T {
    const [debouncedValue, setDebouncedValue] = useState<T>(value);

    useEffect(() => {
        const handler = setTimeout(() => {
            setDebouncedValue(value);
        }, delay);

        return () => {
            clearTimeout(handler);
        };
    }, [value, delay]);

    return debouncedValue;
}
