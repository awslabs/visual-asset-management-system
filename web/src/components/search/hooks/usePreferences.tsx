/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect, useCallback } from 'react';
import { SearchPreferences, DEFAULT_PREFERENCES, FilterPreset } from '../types';

const PREFERENCES_COOKIE_KEY = 'vams-search-preferences';
const COOKIE_EXPIRY_DAYS = 365;

/**
 * Custom hook for managing search preferences with cookie persistence
 */
export const usePreferences = () => {
    const [preferences, setPreferences] = useState<SearchPreferences>(DEFAULT_PREFERENCES);
    const [isLoaded, setIsLoaded] = useState(false);
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

    // Load preferences from cookie on mount
    useEffect(() => {
        loadPreferences();
    }, []);

    const loadPreferences = useCallback(() => {
        try {
            const cookieValue = getCookie(PREFERENCES_COOKIE_KEY);
            if (cookieValue) {
                const savedPreferences = JSON.parse(cookieValue);
                // Merge with defaults to handle new preference fields
                const mergedPreferences = {
                    ...DEFAULT_PREFERENCES,
                    ...savedPreferences,
                };
                setPreferences(mergedPreferences);
            }
        } catch (error) {
            console.warn('Failed to load search preferences from cookie:', error);
            setPreferences(DEFAULT_PREFERENCES);
        } finally {
            setIsLoaded(true);
        }
    }, []);

    const savePreferences = useCallback((newPreferences?: SearchPreferences) => {
        const prefsToSave = newPreferences || preferences;
        try {
            const cookieValue = JSON.stringify(prefsToSave);
            setCookie(PREFERENCES_COOKIE_KEY, cookieValue, COOKIE_EXPIRY_DAYS);
            setHasUnsavedChanges(false);
            return true;
        } catch (error) {
            console.error('Failed to save search preferences to cookie:', error);
            return false;
        }
    }, [preferences]);

    const updatePreferences = useCallback((updates: Partial<SearchPreferences>) => {
        setPreferences(prev => {
            const updated = { ...prev, ...updates };
            setHasUnsavedChanges(true);
            return updated;
        });
    }, []);

    const resetPreferences = useCallback(() => {
        setPreferences(DEFAULT_PREFERENCES);
        deleteCookie(PREFERENCES_COOKIE_KEY);
        setHasUnsavedChanges(false);
    }, []);

    const addFilterPreset = useCallback((name: string, filters: any, metadataFilters: any) => {
        const newPreset: FilterPreset = {
            id: `preset-${Date.now()}`,
            name,
            filters,
            metadataFilters,
            createdAt: new Date().toISOString(),
        };

        updatePreferences({
            filterPresets: [...preferences.filterPresets, newPreset],
        });

        return newPreset;
    }, [preferences.filterPresets, updatePreferences]);

    const removeFilterPreset = useCallback((presetId: string) => {
        updatePreferences({
            filterPresets: preferences.filterPresets.filter(preset => preset.id !== presetId),
        });
    }, [preferences.filterPresets, updatePreferences]);

    const updateFilterPreset = useCallback((presetId: string, updates: Partial<FilterPreset>) => {
        updatePreferences({
            filterPresets: preferences.filterPresets.map(preset =>
                preset.id === presetId ? { ...preset, ...updates } : preset
            ),
        });
    }, [preferences.filterPresets, updatePreferences]);

    // Auto-save preferences when they change (debounced)
    useEffect(() => {
        if (!isLoaded || !hasUnsavedChanges) return;

        const timeoutId = setTimeout(() => {
            savePreferences();
        }, 1000); // Auto-save after 1 second of inactivity

        return () => clearTimeout(timeoutId);
    }, [preferences, isLoaded, hasUnsavedChanges, savePreferences]);

    return {
        preferences,
        isLoaded,
        hasUnsavedChanges,
        updatePreferences,
        savePreferences,
        resetPreferences,
        loadPreferences,
        addFilterPreset,
        removeFilterPreset,
        updateFilterPreset,
    };
};

// Cookie utility functions
function setCookie(name: string, value: string, days: number) {
    const expires = new Date();
    expires.setTime(expires.getTime() + days * 24 * 60 * 60 * 1000);
    document.cookie = `${name}=${value};expires=${expires.toUTCString()};path=/;SameSite=Strict`;
}

function getCookie(name: string): string | null {
    const nameEQ = name + '=';
    const ca = document.cookie.split(';');
    for (let i = 0; i < ca.length; i++) {
        let c = ca[i];
        while (c.charAt(0) === ' ') c = c.substring(1, c.length);
        if (c.indexOf(nameEQ) === 0) return c.substring(nameEQ.length, c.length);
    }
    return null;
}

function deleteCookie(name: string) {
    document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/;`;
}

export default usePreferences;
