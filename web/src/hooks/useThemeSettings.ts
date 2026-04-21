/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect } from "react";
import { applyMode, Mode } from "@cloudscape-design/global-styles";

export type ThemeMode = "light" | "dark";

export function useThemeSettings() {
    const [theme, setTheme] = useState<ThemeMode>(
        () => (localStorage.getItem("vams-theme-preference") as ThemeMode) || "dark"
    );

    // Apply theme mode — both Cloudscape's applyMode AND our CSS class
    useEffect(() => {
        applyMode(theme === "dark" ? Mode.Dark : Mode.Light);
        localStorage.setItem("vams-theme-preference", theme);

        // Also set on <html> and <body> for our theme.css variables and to prevent flash on reload
        const targets = [document.documentElement, document.body];
        if (theme === "dark") {
            targets.forEach((el) => {
                el.classList.add("awsui-dark-mode");
                el.style.backgroundColor = "#0f1b2a";
                el.style.colorScheme = "dark";
            });
        } else {
            targets.forEach((el) => {
                el.classList.remove("awsui-dark-mode");
                el.style.backgroundColor = "";
                el.style.colorScheme = "";
            });
        }
    }, [theme]);

    return { theme, setTheme };
}
