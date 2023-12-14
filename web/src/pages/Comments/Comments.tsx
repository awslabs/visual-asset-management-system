/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { useNavigate } from "react-router-dom";
import { fetchAllAssets, fetchDatabaseAssets } from "../../services/APIService";
import CommentsList from "./CommentsList";
import { useParams } from "react-router";
import Synonyms from "../../synonyms";
import "./Comments.css";
import { AssetListDefinitionCommentPage } from "../../components/list/list-definitions/AssetListDefinitionCommentPage";
import CommentListPage from "../CommentListPage";

export interface CommentType {
    assetId: string;
    "assetVersionId:commentId": string;
    commentBody: string;
    commentOwnerID: string;
    commentOwnerUsername: string;
    dateCreated: string;
    dateEdited?: string;
}

export interface AssetType {
    assetId: string;
    assetLocation: Object;
    assetName: string;
    assetType: string;
    currentVersion: {
        Comment: string;
        dateModified: string;
        S3Version: string;
        Version: string;
        description: string;
    };
    databaseId: string;
    description: string;
    executionId: string;
    isDistributable: boolean;
    pipelineId: string;
    specifiedPipelines: Array<any>;
    versions?: Array<any>;
}

export default function Assets() {
    const [selectedItems, setSelectedItems] = React.useState<Array<Object>>([{}]);
    const navigate = useNavigate();
    const urlParams = useParams();

    return (
        <div className="container">
            <div className="assetSelection">
                <CommentListPage
                    singularName={Synonyms.Asset}
                    singularNameTitleCase={Synonyms.Asset}
                    pluralName={Synonyms.assets}
                    pluralNameTitleCase={Synonyms.Assets}
                    onCreateCallback={() => {
                        if (urlParams.databaseId) {
                            navigate(`/upload/${urlParams.databaseId}`);
                        } else {
                            navigate("/upload");
                        }
                    }}
                    onSelection={setSelectedItems}
                    selectedItems={selectedItems}
                    listDefinition={AssetListDefinitionCommentPage}
                    fetchAllElements={fetchAllAssets}
                    fetchElements={fetchDatabaseAssets}
                />
            </div>
            <div className="commentSectionContainer">
                <CommentsList selectedItems={selectedItems} />
            </div>
        </div>
    );
}
