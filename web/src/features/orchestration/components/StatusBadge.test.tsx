/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import StatusBadge from "./StatusBadge";

describe("StatusBadge", () => {
    it("renders ABORTED distinctly from FAILED", () => {
        const { rerender } = render(<StatusBadge status="ABORTED" />);
        expect(screen.getByText(/aborted/i)).toBeInTheDocument();
        rerender(<StatusBadge status="FAILED" />);
        expect(screen.getByText(/failed/i)).toBeInTheDocument();
    });

    it("renders SUCCEEDED status with label", () => {
        render(<StatusBadge status="SUCCEEDED" />);
        expect(screen.getByText(/succeeded/i)).toBeInTheDocument();
    });

    it("renders RUNNING status with label", () => {
        render(<StatusBadge status="RUNNING" />);
        expect(screen.getByText(/running/i)).toBeInTheDocument();
    });

    it("renders an unmapped Step Functions status without throwing", () => {
        render(<StatusBadge status={"PENDING_REDRIVE" as any} />);
        expect(screen.getByText("PENDING_REDRIVE")).toBeInTheDocument();
    });

    it("renders an empty status as Unknown", () => {
        render(<StatusBadge status={"" as any} />);
        expect(screen.getByText("Unknown")).toBeInTheDocument();
    });
});
