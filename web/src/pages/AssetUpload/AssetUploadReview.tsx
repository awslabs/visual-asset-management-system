/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    Button,
    ColumnLayout,
    Container,
    Header,
    SpaceBetween,
} from "@cloudscape-design/components";
import Synonyms from "../../synonyms";
import { FileUploadTable, shortenBytes } from "./FileUploadTable";
import { DisplayKV } from "./components";
import { useAssetUploadState } from "./state";

import type { AssetDetail, FileUploadTableItem } from "./types";
import type { Metadata } from "../../components/single/Metadata";

export const AssetUploadReview = ({
    metadata,
    setActiveStepIndex,
}: {
    metadata: Metadata;
    setActiveStepIndex: (step: number) => void;
}) => {
    const [state] = useAssetUploadState();

    return (
        <SpaceBetween size="xs">
            <Header
                variant="h3"
                actions={<Button onClick={() => setActiveStepIndex(0)}>Edit</Button>}
            >
                Review
            </Header>
            <Container header={<Header variant="h2">{Synonyms.Asset} Detail</Header>}>
                <ColumnLayout columns={2} variant="text-grid">
                    {Object.keys(state)
                        .filter((k) => k !== "Asset" && k !== "DirectoryHandle")
                        .sort()
                        .map((k) => (
                            <DisplayKV key={k} label={k} value={state[k as keyof AssetDetail]} />
                        ))}
                </ColumnLayout>
            </Container>

            <Container header={<Header variant="h2">{Synonyms.Asset} Metadata</Header>}>
                <ColumnLayout columns={2} variant="text-grid">
                    {Object.keys(metadata).map((k) => (
                        <DisplayKV key={k} label={k} value={metadata[k as keyof Metadata]} />
                    ))}
                </ColumnLayout>
            </Container>
            {state.Asset && (
                <FileUploadTable
                    allItems={state.Asset}
                    resume={false}
                    showCount={false}
                    columnDefinitions={[
                        {
                            id: "filepath",
                            header: "Path",
                            cell: (item: FileUploadTableItem) => item.relativePath,
                            sortingField: "filepath",
                            isRowHeader: true,
                        },
                        {
                            id: "filesize",
                            header: "Size",
                            cell: (item: FileUploadTableItem) =>
                                item.total ? shortenBytes(item.total) : "0b",
                            sortingField: "filesize",
                            isRowHeader: true,
                        },
                    ]}
                />
            )}
        </SpaceBetween>
    );
};
