/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComplianceSchemaEditor from "./ComplianceSchemaEditor";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => <div>Monaco Editor Mock</div>,
    loader: { config: jest.fn() },
}));

const pipelineBody = (inputFiles?: Record<string, any>) => ({
    schemaFormat: "vams-rules-v1",
    rules: {
        "conversion-succeeds": {
            ruleType: "pipeline",
            enforcement: "quarantine",
            pipelineRef: {
                databaseId: "GLOBAL",
                workflowId: "conversion-3d-basic",
                pipelineDatabaseId: "GLOBAL",
                pipelineId: "conversion-3d-basic",
                templateId: "convert-to-glb",
            },
            ...(inputFiles ? { inputFiles } : {}),
            checks: [
                {
                    name: "succeeded",
                    outputField: "execution_success",
                    tolerance: { operator: "eq", value: 1 },
                },
            ],
        },
    },
});

/** The editor as its page uses it: the emitted JSON is fed back as the next value. */
function Harness({ initial, onChange }: { initial: any; onChange: (json: string) => void }) {
    const [value, setValue] = useState(JSON.stringify(initial, null, 2));
    return (
        <ComplianceSchemaEditor
            value={value}
            onChange={(json) => {
                setValue(json);
                onChange(json);
            }}
        />
    );
}

const lastRule = (onChange: jest.Mock) => {
    const json = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    return JSON.parse(json).rules["conversion-succeeds"];
};

const openBuilder = async () => {
    await userEvent.click(screen.getByRole("tab", { name: "Visual Builder" }));
    await screen.findByText("Compliance Rules");
};

describe("ComplianceSchemaEditor input files", () => {
    it("shows the matching selection by default with its glob list", async () => {
        render(<Harness initial={pipelineBody()} onChange={jest.fn()} />);
        await openBuilder();

        expect(screen.getByLabelText("Matching files")).toBeChecked();
        expect(screen.getByLabelText("Whole asset")).not.toBeChecked();
        expect(screen.getByLabelText("Explicit files")).not.toBeChecked();
        expect(screen.getByText("File globs (optional)")).toBeInTheDocument();
        expect(
            screen.getByText(/after the workflow's and the pipeline's own input filters/)
        ).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Add glob" })).toBeInTheDocument();
        expect(screen.queryByText("File paths")).not.toBeInTheDocument();
    });

    it("writes a whole-asset selection and hides the lists", async () => {
        const onChange = jest.fn();
        render(<Harness initial={pipelineBody()} onChange={onChange} />);
        await openBuilder();

        await userEvent.click(screen.getByLabelText("Whole asset"));

        await waitFor(() => {
            expect(lastRule(onChange).inputFiles).toEqual({ mode: "wholeAsset" });
        });
        expect(screen.queryByText("File globs (optional)")).not.toBeInTheDocument();
        expect(screen.queryByText("File paths")).not.toBeInTheDocument();
    });

    it("writes the globs of a matching selection", async () => {
        const onChange = jest.fn();
        render(<Harness initial={pipelineBody()} onChange={onChange} />);
        await openBuilder();

        await userEvent.click(screen.getByRole("button", { name: "Add glob" }));
        await userEvent.type(screen.getByLabelText("Glob pattern 1"), "*.stl");

        await waitFor(() => {
            expect(lastRule(onChange).inputFiles).toEqual({ mode: "matching", filter: ["*.stl"] });
        });
    });

    it("requires a path for an explicit selection and writes the keys once given", async () => {
        const onChange = jest.fn();
        render(<Harness initial={pipelineBody()} onChange={onChange} />);
        await openBuilder();

        await userEvent.click(screen.getByLabelText("Explicit files"));
        expect(screen.getByText("List at least one file path")).toBeInTheDocument();
        await waitFor(() => {
            expect(lastRule(onChange).inputFiles).toEqual({ mode: "explicit", keys: [] });
        });

        await userEvent.click(screen.getByRole("button", { name: "Add file path" }));
        await userEvent.type(screen.getByLabelText("File path 1"), "models/part.stl");
        expect(screen.getByText(/Each path must begin with \//)).toBeInTheDocument();

        await userEvent.clear(screen.getByLabelText("File path 1"));
        await userEvent.type(screen.getByLabelText("File path 1"), "/models/part.stl");
        await waitFor(() => {
            expect(lastRule(onChange).inputFiles).toEqual({
                mode: "explicit",
                keys: ["/models/part.stl"],
            });
        });
        expect(screen.queryByText(/Each path must begin with \//)).not.toBeInTheDocument();
    });

    it("loads an existing explicit selection into the builder", async () => {
        render(
            <Harness
                initial={pipelineBody({ mode: "explicit", keys: ["/a.stl", "/b.stl"] })}
                onChange={jest.fn()}
            />
        );
        await openBuilder();

        expect(screen.getByLabelText("Explicit files")).toBeChecked();
        expect(screen.getByLabelText("File path 1")).toHaveValue("/a.stl");
        expect(screen.getByLabelText("File path 2")).toHaveValue("/b.stl");
        const remove = screen.getByRole("button", { name: "Remove file path /a.stl" });
        expect(within(remove).getByText("Remove")).toBeInTheDocument();
    });
});
