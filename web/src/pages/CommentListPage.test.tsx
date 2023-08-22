/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { render } from "@testing-library/react";
import CommentListPage from "./CommentListPage";
import { Router, useParams } from "react-router";
import { AssetListDefinitionCommentPage } from "../components/list/list-definitions/AssetListDefinitionCommentPage";
import Synonyms from "../synonyms";

// mock the react-router module so that we can create our own return for useParams
jest.mock("react-router", () => ({
    ...jest.requireActual("react-router"),
    useParams: jest.fn(),
}));

describe("Rendering CommentListPage", () => {
    test("Should render correctly WITH databaseId", () => {
        // mock useParams to return a databaseId
        jest.mocked(useParams).mockReturnValue({ databaseId: "test-id" });
        const fetchElements = jest.fn().mockImplementation((databaseId) => {});
        const fetchAllElements = () => {
            console.log("fetching all elements");
        };
        render(
            <CommentListPage
                singularName={Synonyms.Asset}
                singularNameTitleCase={Synonyms.Asset}
                pluralName={Synonyms.assets}
                pluralNameTitleCase={Synonyms.Assets}
                onCreateCallback={() => {}}
                onSelection={() => {}}
                selectedItems={[]}
                listDefinition={AssetListDefinitionCommentPage}
                fetchAllElements={fetchAllElements}
                fetchElements={fetchElements}
            />
        );
        // Should call fetchElements when there is a databaseId
        expect(fetchElements).toBeCalled();
    });

    test("Should render correctly WITHOUT databaseId", () => {
        // mock useParams to return nothing
        jest.mocked(useParams).mockReturnValue({});
        const fetchElements = jest.fn().mockImplementation((databaseId) => {});
        const fetchAllElements = jest.fn().mockImplementation(() => {});
        render(
            <CommentListPage
                singularName={Synonyms.Asset}
                singularNameTitleCase={Synonyms.Asset}
                pluralName={Synonyms.assets}
                pluralNameTitleCase={Synonyms.Assets}
                onCreateCallback={() => {}}
                onSelection={() => {}}
                selectedItems={[]}
                listDefinition={AssetListDefinitionCommentPage}
                fetchAllElements={fetchAllElements}
                fetchElements={fetchElements}
            />
        );
        // should call fetchAllElements when there is not a databaseId
        expect(fetchAllElements).toBeCalled();
    });
});
