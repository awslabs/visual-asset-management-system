/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Online3D viewer starts in the VAMS theme and follows it when the user switches themes.
 */
import React from "react";
import { render, screen, act, waitFor } from "@testing-library/react";
import { ViewerProvider, useViewerContext } from "./ViewerContext";

const Probe: React.FC = () => {
    const { settings, switchTheme } = useViewerContext();
    return (
        <>
            <div data-testid="theme">{settings.themeId}</div>
            <div data-testid="background">{JSON.stringify(settings.backgroundColor)}</div>
            <button onClick={() => switchTheme("light")}>Light override</button>
        </>
    );
};

const mount = () =>
    render(
        <ViewerProvider>
            <Probe />
        </ViewerProvider>
    );

afterEach(() => {
    document.body.classList.remove("awsui-dark-mode");
    localStorage.clear();
});

describe("Online3D viewer theme", () => {
    it("starts dark under the VAMS dark theme and switches with it", async () => {
        document.body.classList.add("awsui-dark-mode");
        mount();
        expect(screen.getByTestId("theme")).toHaveTextContent("dark");
        expect(screen.getByTestId("background")).toHaveTextContent('{"r":42,"g":42,"b":42}');

        act(() => {
            document.body.classList.remove("awsui-dark-mode");
        });
        await waitFor(() => expect(screen.getByTestId("theme")).toHaveTextContent("light"));
    });

    it("starts light under the VAMS light theme", () => {
        mount();
        expect(screen.getByTestId("theme")).toHaveTextContent("light");
    });

    it("keeps a toolbar override until the VAMS theme changes", async () => {
        document.body.classList.add("awsui-dark-mode");
        mount();
        act(() => screen.getByText("Light override").click());
        expect(screen.getByTestId("theme")).toHaveTextContent("light");

        // An unrelated body class change leaves the override alone.
        act(() => {
            document.body.classList.add("some-other-class");
        });
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(screen.getByTestId("theme")).toHaveTextContent("light");
        document.body.classList.remove("some-other-class");
    });
});
