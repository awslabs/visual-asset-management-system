/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import LoadingIcons from "react-loading-icons";
import { useEffect, useState } from "react";
import { Box, Tabs, TextFilter, Button, TextContent } from "@cloudscape-design/components";
import ReactQuill from "react-quill";
import "react-quill/dist/quill.snow.css";
import PopulateComments from "./PopulateComments";
import { API } from "aws-amplify";
import { generateUUID } from "../../common/utils/utils";
import { fetchAllComments } from "../../services/APIService";
import { Auth } from "aws-amplify";

export default function CommentsList(props) {
    const { selectedItems } = props;

    const [value, setValue] = useState("");
    const [reload, setReload] = useState(false);
    const [loading, setLoading] = useState(false);
    const [displayItemsWhileLoading, setDisplayItemsWhileLoading] = useState(false);
    const [displayedSelectedItems, setDisplayedSelectedItems] = useState([]);
    const [allItems, setAllItems] = useState([]);
    const [userId, setUserId] = useState("");

    if (selectedItems !== displayedSelectedItems) {
        setDisplayedSelectedItems(selectedItems);
        setReload(true);
    }

    const selectedItem = displayedSelectedItems[0];
    const assetId = selectedItem?.assetId;

    useEffect(() => {
        const getUserId = async () => {
            try {
                let user = await Auth.currentUserInfo();
                setUserId(user.attributes.sub);
            } catch {
                setUserId("");
            }
        };
        const getData = async () => {
            if (!displayItemsWhileLoading) {
                setAllItems([]);
            }

            let items;
            if (assetId) {
                setLoading(true);
                items = await fetchAllComments({ assetId: assetId });
            }

            if (items !== false && Array.isArray(items)) {
                setLoading(false);
                setReload(false);
                setDisplayItemsWhileLoading(false);
                setAllItems(items.filter((item) => item.assetId.indexOf("#deleted") === -1));
            }
        };
        if (reload) {
            getUserId();
            getData();
        }
    }, [reload, assetId, displayItemsWhileLoading]);

    // modules for Quill (which types of formatting are allowed)
    let modules = {
        toolbar: [
            ["bold", "italic", "underline", "strike"],
            [{ list: "ordered" }, { list: "bullet" }],
            ["link"],
            ["clean"],
        ],
    };

    let formats = ["bold", "italic", "underline", "strike", "list", "bullet", "link"];

    // Add a comment to the database based on the event that is passed to the function
    const addComment = async (event) => {
        // prevent page from reloading
        event.preventDefault();
        setValue("");
        const assetId = selectedItems[0].assetId;
        const versionId = selectedItems[0].currentVersion.S3Version;
        const uuid = "x" + generateUUID();
        const assetVersionIdAndCommentId = `${versionId}:${uuid}`;
        // call the API to post the comment
        let response;
        setDisplayItemsWhileLoading(true);
        setLoading(true);
        try {
            response = await API.post(
                "api",
                `comments/assets/${assetId}/assetVersionId:commentId/${assetVersionIdAndCommentId}`,
                {
                    body: {
                        commentBody: value,
                    },
                }
            );
            console.log(response);
        } catch (e) {
            console.log("create comment error");
            console.log(e);
        } finally {
            setReload(true);
        }
    };

    const showLoading = (keepItemsDisplayed) => {
        setDisplayItemsWhileLoading(keepItemsDisplayed);
        setLoading(true);
    };

    // if no asset is selected, disable the textbox and submit button
    var disableComments = selectedItems[0].assetId === undefined ? true : false;
    return (
        <div>
            <Box padding={{ top: "m", horizontal: "l" }}>
                <div className="commentsHeader">
                    <TextContent>
                        <h1>Comments</h1>
                    </TextContent>
                </div>
                <div className="commentSectionDiv">
                    <Tabs
                        tabs={[
                            {
                                label: "Comments",
                                id: "first",
                                content: (
                                    <div>
                                        <table className="commentSectionTable commentSectionTableBorder">
                                            <tbody>
                                                <tr>
                                                    <td>
                                                        <TextFilter />
                                                    </td>
                                                </tr>
                                                <tr className="commentSectionAssetsContainerTable">
                                                    <td>
                                                        <div className="commentSectionAssetsData">
                                                            <PopulateComments
                                                                loading={loading}
                                                                showLoading={showLoading}
                                                                userId={userId}
                                                                selectedItems={selectedItems}
                                                                allComments={allItems}
                                                                setReload={setReload}
                                                            />
                                                            <div
                                                                className="center"
                                                                hidden={
                                                                    !loading ||
                                                                    displayItemsWhileLoading
                                                                }
                                                            >
                                                                <LoadingIcons.SpinningCircles
                                                                    className="center"
                                                                    fill="#000716"
                                                                />
                                                            </div>
                                                        </div>
                                                    </td>
                                                </tr>
                                                <tr>
                                                    <td className="commentSectionTableBorder">
                                                        <div>
                                                            <form onSubmit={addComment}>
                                                                <div className="container">
                                                                    {/* Documentation: https://github.com/zenoamaro/react-quill */}
                                                                    <div className="commentSectionTextBoxContainer">
                                                                        <ReactQuill
                                                                            theme="snow"
                                                                            value={value}
                                                                            onChange={setValue}
                                                                            id="commentInput"
                                                                            readOnly={
                                                                                disableComments
                                                                            }
                                                                            modules={modules}
                                                                            formats={formats}
                                                                        />
                                                                    </div>
                                                                    <div className="commentSectionSubmitButton">
                                                                        <Button
                                                                            variant="primary"
                                                                            disabled={
                                                                                disableComments ||
                                                                                value.replace(
                                                                                    /(<([^>]+)>)/gi,
                                                                                    ""
                                                                                ) === ""
                                                                            }
                                                                        >
                                                                            Submit
                                                                        </Button>
                                                                    </div>
                                                                </div>
                                                            </form>
                                                        </div>
                                                    </td>
                                                </tr>
                                            </tbody>
                                        </table>
                                    </div>
                                ),
                            },
                            {
                                label: "Second tab",
                                id: "second",
                                content: "Second tab content area",
                                disabled: true,
                            },
                            {
                                label: "Third tab",
                                id: "third",
                                content: "Third tab content area",
                                disabled: true,
                            },
                        ]}
                        variant="container"
                    />
                </div>
            </Box>
        </div>
    );
}
