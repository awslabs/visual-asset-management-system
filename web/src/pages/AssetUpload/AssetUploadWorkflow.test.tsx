/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The upload workflow's error surface.
 *
 * UploadManager reports a failure that escapes its per-step handling through `onError`. The
 * workflow used to hand it a console-only callback while keeping an `uploadError` state that
 * nothing set or rendered, so such a failure left the screen sitting in whatever state it was in.
 * A stub UploadManager records its props so each test can fire the callback it wants and assert
 * the failure is visible, dismissible, and absent on the success path.
 */

import React from "react";
import { render, screen, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import AssetUploadWorkflow from "./AssetUploadWorkflow";

// The real UploadManager starts the upload on mount through the service layer; the stub only
// captures its props. Its factory must not reference JSX or imports, so it renders nothing.
let mockManagerProps: any;
jest.mock("./UploadManager", () => ({
    __esModule: true,
    default: (props: any) => {
        mockManagerProps = props;
        return null;
    },
}));
jest.mock("../../services/AssetUploadService", () => ({}));

const assetDetail = { databaseId: "db-1", assetName: "model" } as any;

const mount = () =>
    render(
        <MemoryRouter>
            <AssetUploadWorkflow
                assetDetail={assetDetail}
                metadata={{}}
                fileItems={[]}
                onComplete={jest.fn()}
                onCancel={jest.fn()}
            />
        </MemoryRouter>
    );

describe("AssetUploadWorkflow error surface", () => {
    beforeEach(() => {
        mockManagerProps = undefined;
    });

    it("shows no error alert before anything is reported", () => {
        mount();
        expect(mockManagerProps).toBeDefined();
        expect(screen.queryByText("Upload Failed")).not.toBeInTheDocument();
    });

    it("renders an error routed through onError", () => {
        mount();
        act(() => mockManagerProps.onError(new Error("S3 rejected part 3 of model.glb")));

        expect(screen.getByText("Upload Failed")).toBeInTheDocument();
        expect(screen.getByText("S3 rejected part 3 of model.glb")).toBeInTheDocument();
    });

    it("falls back to a generic message when the error carries none", () => {
        mount();
        act(() => mockManagerProps.onError(new Error()));

        expect(screen.getByText("Upload Failed")).toBeInTheDocument();
        expect(screen.getByText(/upload could not be completed/)).toBeInTheDocument();
    });

    it("clears the alert when dismissed", () => {
        const { container } = mount();
        act(() => mockManagerProps.onError(new Error("boom")));

        const alert = createWrapper(container).findAlert();
        expect(alert).toBeTruthy();
        act(() => {
            alert?.findDismissButton()?.click();
        });

        expect(screen.queryByText("Upload Failed")).not.toBeInTheDocument();
    });

    it("shows the completion message and no error surface when the upload succeeds", () => {
        // Paired arm for the tests above: the error alert is tied to onError, not to any
        // completion state.
        mount();
        act(() =>
            mockManagerProps.onUploadComplete({
                assetId: "asset-1",
                uploadId: "u-1",
                message: "ok",
                fileResults: [],
                overallSuccess: true,
            })
        );

        expect(screen.getByText("Upload Complete")).toBeInTheDocument();
        expect(screen.queryByText("Upload Failed")).not.toBeInTheDocument();
    });
});
