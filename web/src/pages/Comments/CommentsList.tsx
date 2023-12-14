/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import LoadingIcons from "react-loading-icons";
import { useEffect, useState, useRef } from "react";
import { Box, Tabs, Button, TextContent } from "@cloudscape-design/components";
import PopulateComments from "./PopulateComments";
import { API } from "aws-amplify";
import { generateUUID } from "../../common/utils/utils";
import { fetchAllComments } from "../../services/APIService";
import { Auth } from "aws-amplify";
import JoditEditor from "jodit-react";

export default function CommentsList(props: any) {
    const { selectedItems } = props;

    const [reload, setReload] = useState<boolean>(false);
    const [loading, setLoading] = useState<boolean>(false);
    const [showLoadingIcon, setShowLoadingIcon] = useState<boolean>(false);
    const [displayItemsWhileLoading, setDisplayItemsWhileLoading] = useState<boolean>(false);
    const [displayedSelectedItems, setDisplayedSelectedItems] = useState<Array<any>>([]);
    const [allItems, setAllItems] = useState<Array<any>>([]);
    const [userId, setUserId] = useState<string>("");
    const [content, setContent] = useState<string>("");
    const editor = useRef(null);
    const listInnerRef = useRef(null);

    if (selectedItems !== displayedSelectedItems) {
        setDisplayedSelectedItems(selectedItems);
        setReload(true);
    }

    // if no asset is selected, disable the textbox and submit button
    let disableComments = selectedItems[0].assetId === undefined ? true : false;

    const config = {
        readonly: disableComments,
        minHeight: 100,
        showCharsCounter: false,
        showWordsCounter: false,
        showXPathInStatusbar: false,
        maxWidth: "auto",
        placeholder: "",
        buttons: [
            "bold",
            "italic",
            "strikethrough",
            "underline",
            "|",
            "ul",
            "ol",
            "link",
            "|",
            "eraser",
        ],
        buttonsMD: [
            "bold",
            "italic",
            "strikethrough",
            "underline",
            "|",
            "ul",
            "ol",
            "link",
            "|",
            "eraser",
        ],
        buttonsXS: [
            "bold",
            "italic",
            "strikethrough",
            "underline",
            "|",
            "ul",
            "ol",
            "link",
            "|",
            "eraser",
        ],
    };

    const handleUpdate = (event: string) => {
        const editorContent = event;
        setContent(editorContent);
    };

    const selectedItem: any = displayedSelectedItems[0];
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
                setShowLoadingIcon(false);
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

    // Add a comment to the database based on the event that is passed to the function
    const addComment = async (event: { preventDefault: () => void }) => {
        // prevent page from reloading
        event.preventDefault();
        if (content.replace(/(<([^>]+)>)/gi, "") === "") {
            console.log("empty textbox, not adding comment");
            return;
        }
        setContent("");
        const assetId = selectedItems[0].assetId;
        const versionId = selectedItems[0].currentVersion.S3Version;
        const uuid = "x" + generateUUID();
        const assetVersionIdAndCommentId = `${versionId}:${uuid}`;
        // call the API to post the comment
        let response;
        setDisplayItemsWhileLoading(true);
        setLoading(true);
        setShowLoadingIcon(true);
        try {
            response = await API.post(
                "api",
                `comments/assets/${assetId}/assetVersionId:commentId/${assetVersionIdAndCommentId}`,
                {
                    body: {
                        commentBody: content,
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

    const showLoading = (keepItemsDisplayed: boolean | ((prevState: boolean) => boolean)) => {
        setDisplayItemsWhileLoading(keepItemsDisplayed);
        setLoading(true);
    };

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
                                                <tr className="commentSectionAssetsContainerTable">
                                                    <td>
                                                        <div
                                                            className="populateCommentsDiv"
                                                            ref={listInnerRef}
                                                        >
                                                            <div
                                                                hidden={
                                                                    (!loading ||
                                                                        displayItemsWhileLoading) &&
                                                                    !showLoadingIcon
                                                                }
                                                            >
                                                                <LoadingIcons.SpinningCircles
                                                                    className="center"
                                                                    fill="#000716"
                                                                />
                                                            </div>
                                                            <PopulateComments
                                                                loading={loading}
                                                                showLoading={showLoading}
                                                                userId={userId}
                                                                selectedItems={selectedItems}
                                                                allComments={allItems}
                                                                setReload={setReload}
                                                            />
                                                        </div>
                                                    </td>
                                                </tr>
                                                <tr>
                                                    <td className="commentSectionTableBorder">
                                                        <div>
                                                            <form onSubmit={addComment}>
                                                                <div className="container">
                                                                    <div className="commentSectionTextBoxContainer">
                                                                        <JoditEditor
                                                                            ref={editor}
                                                                            value={content}
                                                                            config={config}
                                                                            onBlur={handleUpdate}
                                                                            onChange={(
                                                                                newContent
                                                                            ) => {}}
                                                                        />
                                                                    </div>
                                                                    <div className="commentSectionSubmitButton">
                                                                        <Button
                                                                            data-testid="submitButton"
                                                                            variant="primary"
                                                                            disabled={
                                                                                disableComments
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
                        ]}
                        variant="container"
                    />
                </div>
            </Box>
        </div>
    );
}
