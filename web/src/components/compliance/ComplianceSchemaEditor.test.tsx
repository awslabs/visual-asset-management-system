/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FormField from "@cloudscape-design/components/form-field";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
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
        expect(screen.getAllByText(/Each path must begin with \//).length).toBeGreaterThan(0);

        await userEvent.clear(screen.getByLabelText("File path 1"));
        await userEvent.type(screen.getByLabelText("File path 1"), "/models/part.stl");
        await waitFor(() => {
            expect(lastRule(onChange).inputFiles).toEqual({
                mode: "explicit",
                keys: ["/models/part.stl"],
            });
        });
        expect(screen.queryAllByText(/Each path must begin with \//)).toHaveLength(0);
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

const emptyBody = { schemaFormat: "vams-rules-v1", rules: {} };

const lastBody = (onChange: jest.Mock) =>
    JSON.parse(onChange.mock.calls[onChange.mock.calls.length - 1][0]);

const ruleBody = (name: string, rule: Record<string, any>) => ({
    schemaFormat: "vams-rules-v1",
    rules: { [name]: rule },
});

const addRule = async (label: string) => {
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    await userEvent.click(await screen.findByText(label));
};

describe("ComplianceSchemaEditor rule types", () => {
    it("offers all six rule types from Add rule", async () => {
        render(<Harness initial={emptyBody} onChange={jest.fn()} />);
        await openBuilder();

        await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
        for (const label of [
            "Pipeline rule",
            "Metadata rule",
            "Relationship rule",
            "File structure rule",
            "Version coverage rule",
            "Asset attributes rule",
        ]) {
            expect(await screen.findByText(label)).toBeInTheDocument();
        }
    });

    it("adds a metadata rule from its example and leaves its checks to the JSON tab", async () => {
        const onChange = jest.fn();
        render(<Harness initial={emptyBody} onChange={onChange} />);
        await openBuilder();

        await addRule("Metadata rule");

        await waitFor(() => {
            expect(lastBody(onChange).rules["metadata-rule-1"]).toMatchObject({
                ruleType: "metadata",
                metadataSchemaRef: { databaseId: "GLOBAL", schemaName: "defaultAsset" },
            });
        });
        expect(screen.getByText(/checks of a metadata rule are edited in the/)).toBeInTheDocument();
    });

    it("adds a file structure rule and writes the patterns typed one per line", async () => {
        const onChange = jest.fn();
        render(<Harness initial={emptyBody} onChange={onChange} />);
        await openBuilder();

        await addRule("File structure rule");
        await waitFor(() => {
            expect(lastBody(onChange).rules["file-structure-rule-1"].ruleType).toBe(
                "fileStructure"
            );
        });
        const required = screen.getByLabelText("Required paths, check 1");
        await userEvent.clear(required);
        await userEvent.type(required, "^models/{Enter}\\.glb$");

        await waitFor(() => {
            expect(lastBody(onChange).rules["file-structure-rule-1"].checks[0]).toMatchObject({
                name: "model-file-present",
                requiredPaths: ["^models/", "\\.glb$"],
            });
        });
        expect(screen.getByLabelText("Forbidden paths, check 1")).toBeInTheDocument();
        expect(screen.getByLabelText("Minimum files, check 1")).toBeInTheDocument();
        expect(screen.getByLabelText("Maximum files, check 1")).toBeInTheDocument();
    });

    it("shows the API's message when minFiles exceeds maxFiles and when no criterion is left", async () => {
        const onChange = jest.fn();
        render(
            <Harness
                initial={ruleBody("files", {
                    ruleType: "fileStructure",
                    enforcement: "warn",
                    checks: [{ name: "count", minFiles: 3 }],
                })}
                onChange={onChange}
            />
        );
        await openBuilder();

        await userEvent.type(screen.getByLabelText("Maximum files, check 1"), "2");
        expect(await screen.findByText("'minFiles' must be <= 'maxFiles'")).toBeInTheDocument();

        await userEvent.clear(screen.getByLabelText("Minimum files, check 1"));
        await userEvent.clear(screen.getByLabelText("Maximum files, check 1"));
        expect(
            await screen.findByText(
                "At least one of 'requiredPaths', 'forbiddenPaths', 'minFiles' or 'maxFiles' is required"
            )
        ).toBeInTheDocument();
        await waitFor(() => {
            expect(lastBody(onChange).rules.files.checks[0]).toEqual({ name: "count" });
        });
    });

    it("adds a version coverage rule with its excluded paths and a second check", async () => {
        const onChange = jest.fn();
        render(<Harness initial={emptyBody} onChange={onChange} />);
        await openBuilder();

        await addRule("Version coverage rule");
        expect(screen.getByLabelText("Excluded paths (optional), check 1")).toHaveValue(
            "\\.previewFile\\."
        );
        await userEvent.click(screen.getByRole("button", { name: "Add check" }));

        await waitFor(() => {
            expect(lastBody(onChange).rules["version-coverage-rule-1"].checks).toEqual([
                {
                    name: "files-captured-in-a-version",
                    description: "Every current file is recorded in an asset version",
                    excludePaths: ["\\.previewFile\\."],
                },
                { name: "check-2" },
            ]);
        });
    });

    it("names each pattern field by its check, inside the modal's Schema Body field too", async () => {
        // The accessible name a screen reader (and Playwright's getByLabel) computes, in which the
        // enclosing FormFields' aria-labelledby outranks the control's aria-label; getByLabelText
        // alone would match the aria-label attribute whatever the computed name.
        render(
            <FormField label="Schema Body">
                <Harness
                    initial={ruleBody("files", {
                        ruleType: "fileStructure",
                        enforcement: "warn",
                        checks: [
                            { name: "first", requiredPaths: ["^a/"] },
                            { name: "second", requiredPaths: ["^b/"], forbiddenPaths: ["\\.tmp$"] },
                        ],
                    })}
                    onChange={jest.fn()}
                />
            </FormField>
        );
        await openBuilder();

        expect(screen.getByRole("textbox", { name: "Required paths, check 1" })).toHaveValue("^a/");
        expect(screen.getByRole("textbox", { name: "Required paths, check 2" })).toHaveValue("^b/");
        expect(screen.getByRole("textbox", { name: "Forbidden paths, check 1" })).toHaveValue("");
        expect(screen.getByRole("textbox", { name: "Forbidden paths, check 2" })).toHaveValue(
            "\\.tmp$"
        );
        expect(screen.queryByRole("textbox", { name: "Schema Body Required paths" })).toBeNull();
    });

    it("names the excluded paths field of a version coverage check by its check", async () => {
        render(
            <FormField label="Schema Body">
                <Harness
                    initial={ruleBody("coverage", {
                        ruleType: "versionCoverage",
                        enforcement: "warn",
                        checks: [{ name: "c1" }, { name: "c2", excludePaths: ["\\.preview$"] }],
                    })}
                    onChange={jest.fn()}
                />
            </FormField>
        );
        await openBuilder();

        expect(
            screen.getByRole("textbox", { name: "Excluded paths (optional), check 1" })
        ).toHaveValue("");
        expect(
            screen.getByRole("textbox", { name: "Excluded paths (optional), check 2" })
        ).toHaveValue("\\.preview$");
    });

    it("edits an asset name pattern and its condition", async () => {
        const onChange = jest.fn();
        render(<Harness initial={emptyBody} onChange={onChange} />);
        await openBuilder();

        await addRule("Asset attributes rule");
        expect(screen.getByLabelText("Must match")).toBeChecked();
        await userEvent.click(screen.getByLabelText("Must not match"));

        await waitFor(() => {
            const check = lastBody(onChange).rules["asset-attributes-rule-1"].checks[0];
            expect(check.notMatches).toBe("^[A-Za-z0-9][A-Za-z0-9_.-]*$");
            expect(check.matches).toBeUndefined();
        });

        await userEvent.clear(screen.getByLabelText("Pattern, check 1"));
        expect(
            await screen.findByText("Every 'notMatches' pattern must be 1 to 512 characters")
        ).toBeInTheDocument();
    });

    it("edits the tags of a tag check as tokens", async () => {
        const onChange = jest.fn();
        render(
            <Harness
                initial={ruleBody("tagged", {
                    ruleType: "assetAttributes",
                    enforcement: "warn",
                    checks: [{ name: "tags", field: "tags", requiredTags: ["engineering"] }],
                })}
                onChange={onChange}
            />
        );
        await openBuilder();

        expect(screen.getByText("engineering")).toBeInTheDocument();
        await userEvent.type(screen.getByLabelText("Forbidden tag, check 1"), "draft");
        await userEvent.click(screen.getByRole("button", { name: "Add forbidden tag, check 1" }));
        await waitFor(() => {
            expect(lastBody(onChange).rules.tagged.checks[0]).toEqual({
                name: "tags",
                field: "tags",
                requiredTags: ["engineering"],
                forbiddenTags: ["draft"],
            });
        });

        await userEvent.click(screen.getByRole("button", { name: "Remove engineering" }));
        await waitFor(() => {
            expect(lastBody(onChange).rules.tagged.checks[0].requiredTags).toBeUndefined();
        });
    });
});

const metadataRule = (extra: Record<string, any> = {}) =>
    ruleBody("m", {
        ruleType: "metadata",
        enforcement: "warn",
        metadataSchemaRef: { databaseId: "GLOBAL", schemaName: "defaultAsset" },
        ...extra,
        checks: [{ name: "c", validateRequired: true }],
    });

/** The Cloudscape Select inside the form field labelled `label`. */
const selectNamed = (label: string) => {
    const select = createWrapper()
        .findAllFormFields()
        .find((field) => field.findLabel()?.getElement().textContent === label)
        ?.findControl()
        ?.findSelect();
    if (!select) throw new Error(`no select in the ${label} field`);
    return select;
};

describe("ComplianceSchemaEditor metadata rule scope", () => {
    it("loads a metadata rule without a scope as asset metadata, with no filters", async () => {
        render(<Harness initial={metadataRule()} onChange={jest.fn()} />);
        await openBuilder();

        expect(selectNamed("Scope").findTrigger().getElement()).toHaveTextContent("Asset metadata");
        expect(screen.queryByLabelText("Include paths, rule 1")).not.toBeInTheDocument();
        expect(screen.queryByText("Link direction")).not.toBeInTheDocument();
        expect(
            screen.getByText(/schema reference and the checks of a metadata rule are edited in the/)
        ).toBeInTheDocument();
    });

    it.each([["files"], ["fileAttributes"]])(
        "shows the include and exclude paths for the %s scope",
        async (scope) => {
            render(
                <Harness
                    initial={metadataRule({ scope, excludePaths: ["\\.previewFile\\."] })}
                    onChange={jest.fn()}
                />
            );
            await openBuilder();

            expect(screen.getByRole("textbox", { name: "Include paths, rule 1" })).toHaveValue("");
            expect(screen.getByRole("textbox", { name: "Exclude paths, rule 1" })).toHaveValue(
                "\\.previewFile\\."
            );
            expect(screen.queryByText("Link direction")).not.toBeInTheDocument();
        }
    );

    it("shows the direction and relationship type for the links scope", async () => {
        render(
            <Harness
                initial={metadataRule({ scope: "links", linkDirection: "parents" })}
                onChange={jest.fn()}
            />
        );
        await openBuilder();

        expect(selectNamed("Link direction").findTrigger().getElement()).toHaveTextContent(
            "Parents (links to this asset)"
        );
        expect(selectNamed("Relationship type").findTrigger().getElement()).toHaveTextContent(
            "Any relationship type"
        );
        expect(screen.queryByLabelText("Include paths, rule 1")).not.toBeInTheDocument();
    });

    it("switches the scope and writes the filters the new scope takes", async () => {
        const onChange = jest.fn();
        render(<Harness initial={metadataRule()} onChange={onChange} />);
        await openBuilder();

        const scope = selectNamed("Scope");
        scope.openDropdown();
        scope.selectOptionByValue("files");
        await waitFor(() => {
            expect(lastBody(onChange).rules.m.scope).toBe("files");
        });
        await userEvent.type(
            screen.getByRole("textbox", { name: "Include paths, rule 1" }),
            "^models/"
        );
        await waitFor(() => {
            expect(lastBody(onChange).rules.m.includePaths).toEqual(["^models/"]);
        });

        const again = selectNamed("Scope");
        again.openDropdown();
        again.selectOptionByValue("links");
        await waitFor(() => {
            const rule = lastBody(onChange).rules.m;
            expect(rule.scope).toBe("links");
            expect(rule.includePaths).toBeUndefined();
        });
        const direction = selectNamed("Link direction");
        direction.openDropdown();
        direction.selectOptionByValue("children");
        await waitFor(() => {
            expect(lastBody(onChange).rules.m.linkDirection).toBe("children");
        });
    });

    it("shows the API's message for a filter the scope does not take", async () => {
        render(
            <Harness initial={metadataRule({ relationshipType: "related" })} onChange={jest.fn()} />
        );
        await openBuilder();

        expect(
            screen.getByText("'linkDirection' and 'relationshipType' apply to the links scope only")
        ).toBeInTheDocument();
    });
});
