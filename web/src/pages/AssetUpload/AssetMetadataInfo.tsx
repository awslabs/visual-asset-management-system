import { Container, Header, SpaceBetween } from "@cloudscape-design/components";
import { useAssetUploadState } from "./state";
import Synonyms from "../../synonyms";
import ControlledMetadata from "../../components/metadata/ControlledMetadata";

import type { Metadata } from "../../components/single/Metadata";
import { useEffect } from "react";
import { z } from "zod";

export const AssetMetadataInfo = ({
    metadata,
    setMetadata,
}: {
    metadata: Metadata;
    setMetadata: (metadata: Metadata) => void;
}) => {
    const [state, dispatch] = useAssetUploadState();

    useEffect(() => {});

    return (
        <Container header={<Header variant="h2">{Synonyms.Asset} Metadata</Header>}>
            <SpaceBetween direction="vertical" size="l">
                <ControlledMetadata
                    assetId={state.assetId || ""}
                    databaseId={state.databaseId || ""}
                    initialState={metadata}
                    store={(databaseId, assetId, record) => {
                        return new Promise((resolve) => {
                            setMetadata(record);
                            resolve(null);
                        });
                    }}
                    data-testid="controlled-metadata-grid"
                />
            </SpaceBetween>
        </Container>
    );
};
