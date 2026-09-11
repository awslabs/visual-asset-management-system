/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Template prefetching for the wizard steps.
 *
 * A wizard's per-step queries only mount when that step renders, so the pipeline step began loading
 * its template list at the moment the user arrived and sat empty for seconds with no indication
 * anything was happening. The pipeline list is known as soon as the workflow resolves, so the lists
 * can be fetched during the earlier steps instead.
 *
 * What matters and is asserted here: the prefetch writes the SAME query keys the step reads (or the
 * step would re-fetch anyway), it does not re-request what is already cached, and a prefetch failure
 * cannot break the wizard.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { usePrefetchPipelineTemplates, qk } from "./queries";

jest.mock("./pipelines", () => ({
    listTemplates: jest.fn(),
    getTemplate: jest.fn(),
}));
jest.mock("./workflows", () => ({}));
jest.mock("./executions", () => ({}));
jest.mock("./databases", () => ({}));
jest.mock("./assets", () => ({}));

const svc = () => require("./pipelines");

function makeClient() {
    return new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
}

// `Inner` is defined ONCE at module scope. Declaring it inside Harness would give it a new identity
// on every render, so React would unmount and remount it — running the session cleanup and making a
// re-render indistinguishable from closing and reopening the wizard.
const Inner: React.FC<{ targets: any[] }> = ({ targets }) => {
    usePrefetchPipelineTemplates(targets);
    return null;
};

const Harness: React.FC<{ targets: any[]; client: QueryClient }> = ({ targets, client }) => (
    <QueryClientProvider client={client}>
        <Inner targets={targets} />
    </QueryClientProvider>
);

describe("usePrefetchPipelineTemplates", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        svc().listTemplates.mockResolvedValue([true, []]);
        svc().getTemplate.mockResolvedValue([true, { templateId: "t1" }]);
    });

    it("fetches the template list for every pipeline up front", async () => {
        const client = makeClient();
        render(
            <Harness
                client={client}
                targets={[
                    { databaseId: "db1", pipelineId: "p1" },
                    { databaseId: "db1", pipelineId: "p2" },
                ]}
            />
        );
        await waitFor(() => expect(svc().listTemplates).toHaveBeenCalledTimes(2));
        expect(svc().listTemplates).toHaveBeenCalledWith("db1", "p1");
        expect(svc().listTemplates).toHaveBeenCalledWith("db1", "p2");
    });

    it("writes the same cache key the step's own query reads", async () => {
        // If the key differed, the step would find nothing and fetch again — the prefetch would be
        // pure waste rather than an optimization.
        const client = makeClient();
        svc().listTemplates.mockResolvedValue([true, [{ templateId: "t1" }]]);
        render(<Harness client={client} targets={[{ databaseId: "db1", pipelineId: "p1" }]} />);

        await waitFor(() =>
            expect(client.getQueryData(qk.templates("db1", "p1"))).toEqual([{ templateId: "t1" }])
        );
    });

    it("also warms the already-chosen template's detail", async () => {
        // The step renders its form from the single-template detail (tagSchema + rehydrated body),
        // not from the list — so an edit/re-run flow otherwise waited on a SECOND serial request.
        const client = makeClient();
        render(
            <Harness
                client={client}
                targets={[{ databaseId: "db1", pipelineId: "p1", defaultTemplateId: "t1" }]}
            />
        );
        await waitFor(() => expect(svc().getTemplate).toHaveBeenCalledWith("db1", "p1", "t1"));
        await waitFor(() =>
            expect(client.getQueryData(qk.template("db1", "p1", "t1"))).toBeDefined()
        );
    });

    it("does not fetch a detail when no template is preselected", async () => {
        // Nothing is chosen yet, so there is no specific template worth fetching.
        const client = makeClient();
        render(<Harness client={client} targets={[{ databaseId: "db1", pipelineId: "p1" }]} />);
        await waitFor(() => expect(svc().listTemplates).toHaveBeenCalled());
        expect(svc().getTemplate).not.toHaveBeenCalled();
    });

    it("does not duplicate a request for an entry already in cache", async () => {
        // prefetchQuery, not fetchQuery: an entry written moments ago (or in flight) must not be
        // re-requested while this wizard session is open.
        const client = makeClient();
        client.setQueryData(qk.templates("db1", "p1"), [{ templateId: "cached" }]);
        render(<Harness client={client} targets={[{ databaseId: "db1", pipelineId: "p1" }]} />);
        await new Promise((r) => setTimeout(r, 20));
        expect(svc().listTemplates).not.toHaveBeenCalled();
    });

    it("skips entries missing a database or pipeline id", async () => {
        const client = makeClient();
        render(
            <Harness
                client={client}
                targets={[
                    { databaseId: "", pipelineId: "p1" },
                    { databaseId: "db1", pipelineId: "" },
                    { databaseId: "db1", pipelineId: "p2" },
                ]}
            />
        );
        await waitFor(() => expect(svc().listTemplates).toHaveBeenCalledTimes(1));
        expect(svc().listTemplates).toHaveBeenCalledWith("db1", "p2");
    });

    it("does not re-fetch when re-rendered with an equivalent target list", async () => {
        // The caller rebuilds the array in a useMemo, so a new array reference per render must not
        // re-trigger the effect.
        const client = makeClient();
        const { rerender } = render(
            <Harness client={client} targets={[{ databaseId: "db1", pipelineId: "p1" }]} />
        );
        await waitFor(() => expect(svc().listTemplates).toHaveBeenCalledTimes(1));
        rerender(<Harness client={client} targets={[{ databaseId: "db1", pipelineId: "p1" }]} />);
        await new Promise((r) => setTimeout(r, 20));
        expect(svc().listTemplates).toHaveBeenCalledTimes(1);
    });

    it("fetches the newly added pipeline when the target set changes", async () => {
        const client = makeClient();
        const { rerender } = render(
            <Harness client={client} targets={[{ databaseId: "db1", pipelineId: "p1" }]} />
        );
        await waitFor(() => expect(svc().listTemplates).toHaveBeenCalledTimes(1));
        rerender(
            <Harness
                client={client}
                targets={[
                    { databaseId: "db1", pipelineId: "p1" },
                    { databaseId: "db1", pipelineId: "p2" },
                ]}
            />
        );
        await waitFor(() => expect(svc().listTemplates).toHaveBeenCalledWith("db1", "p2"));
    });

    it("a failing prefetch does not throw into the wizard", async () => {
        // A prefetch is an optimization; the step's own query is what should surface an error.
        const client = makeClient();
        svc().listTemplates.mockRejectedValue(new Error("network down"));
        expect(() =>
            render(<Harness client={client} targets={[{ databaseId: "db1", pipelineId: "p1" }]} />)
        ).not.toThrow();
        await new Promise((r) => setTimeout(r, 20));
    });

    it("does nothing for an empty target list", async () => {
        const client = makeClient();
        render(<Harness client={client} targets={[]} />);
        await new Promise((r) => setTimeout(r, 20));
        expect(svc().listTemplates).not.toHaveBeenCalled();
    });
});

/**
 * Session scoping.
 *
 * The prefetched data belongs to ONE wizard opening. Templates are editable, and a stale config body
 * silently becomes what a run is launched with — so a later opening must re-read rather than render
 * from an earlier session's snapshot. The QueryClient is app-level, so this requires explicit cleanup
 * on unmount; unmounting the wizard alone would leave the entries behind.
 */
describe("usePrefetchPipelineTemplates session scoping", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        svc().listTemplates.mockResolvedValue([true, [{ templateId: "t1" }]]);
        svc().getTemplate.mockResolvedValue([true, { templateId: "t1" }]);
    });

    it("removes its prefetched entries when the wizard closes", async () => {
        const client = makeClient();
        const { unmount } = render(
            <Harness client={client} targets={[{ databaseId: "db1", pipelineId: "p1" }]} />
        );
        await waitFor(() => expect(client.getQueryData(qk.templates("db1", "p1"))).toBeDefined());

        unmount();
        expect(client.getQueryData(qk.templates("db1", "p1"))).toBeUndefined();
    });

    it("removes the prefetched template detail too", async () => {
        const client = makeClient();
        const { unmount } = render(
            <Harness
                client={client}
                targets={[{ databaseId: "db1", pipelineId: "p1", defaultTemplateId: "t1" }]}
            />
        );
        await waitFor(() =>
            expect(client.getQueryData(qk.template("db1", "p1", "t1"))).toBeDefined()
        );

        unmount();
        expect(client.getQueryData(qk.template("db1", "p1", "t1"))).toBeUndefined();
    });

    it("re-fetches on a second opening rather than reusing the first session's data", async () => {
        // The point of the cleanup: an edited template must be re-read, not served from the snapshot
        // taken when the wizard was last open.
        const client = makeClient();
        const first = render(
            <Harness client={client} targets={[{ databaseId: "db1", pipelineId: "p1" }]} />
        );
        await waitFor(() => expect(svc().listTemplates).toHaveBeenCalledTimes(1));
        first.unmount();

        render(<Harness client={client} targets={[{ databaseId: "db1", pipelineId: "p1" }]} />);
        await waitFor(() => expect(svc().listTemplates).toHaveBeenCalledTimes(2));
    });

    it("leaves cache entries it did not create alone", async () => {
        // A template open elsewhere in the app must survive the wizard closing.
        const client = makeClient();
        client.setQueryData(qk.templates("other-db", "other-p"), [{ templateId: "elsewhere" }]);
        const { unmount } = render(
            <Harness client={client} targets={[{ databaseId: "db1", pipelineId: "p1" }]} />
        );
        await waitFor(() => expect(client.getQueryData(qk.templates("db1", "p1"))).toBeDefined());

        unmount();
        expect(client.getQueryData(qk.templates("other-db", "other-p"))).toEqual([
            { templateId: "elsewhere" },
        ]);
    });

    it("cleans up every pipeline's entry, not just the last", async () => {
        const client = makeClient();
        const { unmount } = render(
            <Harness
                client={client}
                targets={[
                    { databaseId: "db1", pipelineId: "p1" },
                    { databaseId: "db1", pipelineId: "p2" },
                ]}
            />
        );
        await waitFor(() => expect(client.getQueryData(qk.templates("db1", "p2"))).toBeDefined());

        unmount();
        expect(client.getQueryData(qk.templates("db1", "p1"))).toBeUndefined();
        expect(client.getQueryData(qk.templates("db1", "p2"))).toBeUndefined();
    });
});

/**
 * The callers must actually pass their pipeline list to the hook.
 *
 * Both wizards mock `../api/queries` in their own suites, so the hook is a no-op there and a missing
 * call site would go unnoticed. These read the source instead: cheap, and they fail loudly if the
 * wiring is dropped while the hook itself keeps passing its unit tests.
 */
describe("prefetch wiring", () => {
    const read = (p: string) =>
        require("fs").readFileSync(require("path").join(__dirname, p), "utf-8");

    it("the execute wizard prefetches its pipelines' templates", () => {
        const src = read("../wizard/ExecuteWizard.tsx");
        expect(src).toContain("usePrefetchPipelineTemplates(templatePrefetchTargets)");
        // Sourced from the workflow's pipeline refs, so every step is covered — not just the first.
        expect(src).toContain("effectiveWorkflow.specifiedPipelines.map");
    });

    it("the execute wizard includes the preselected template id", () => {
        // Without it, an already-configured step still waits on the detail fetch after the list.
        const src = read("../wizard/ExecuteWizard.tsx");
        expect(src).toMatch(/defaultTemplateId:[\s\S]{0,120}ref\.defaultTemplateId/);
    });

    it("the workflow builder prefetches its pipelines' templates", () => {
        const src = read("../workflows/WorkflowBuilder.tsx");
        expect(src).toContain("usePrefetchPipelineTemplates(templatePrefetchTargets)");
        expect(src).toContain("state.specifiedPipelines");
    });
});
