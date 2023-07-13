/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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

export default function Assets() {
    const [selectedItems, setSelectedItems] = React.useState([{}]);
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
            <div className="inflow">
                <div className="positioner">
                    <div className="fixed vl"></div>
                </div>
            </div>
            <div className="commentSectionContainer">
                <CommentsList
                    key={selectedItems}
                    selectedItems={selectedItems}
                    setSelectedItems={setSelectedItems}
                />
            </div>
        </div>
    );
}
