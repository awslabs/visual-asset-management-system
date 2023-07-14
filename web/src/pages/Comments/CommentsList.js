/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from "react";

import { Box, Tabs, TextFilter, Button } from "@cloudscape-design/components";
import ReactQuill from "react-quill";
import PopulateComments from "./PopulateComments";
import "react-quill/dist/quill.snow.css";
import { API } from "aws-amplify";
import { generateUUID } from "../../common/utils/utils";
import { fetchAllComments } from "../../services/APIService";
import { modelFileFormats } from "../../common/constants/fileFormats";

export default function CommentsList(props) {
    const { selectedItems } = props;

    const [value, setValue] = useState("");
    const [reload, setReload] = useState(true);
    const [displayedSelectedItems, setDisplayedSelectedItems] = useState([]);
    const [allItems, setAllItems] = useState([]);

    if (selectedItems != displayedSelectedItems) {
        setDisplayedSelectedItems(selectedItems);
        setReload(true);
    }

    var selectedItem = displayedSelectedItems[0];
    var assetId;
    if (selectedItem === undefined) {
        assetId = undefined;
    } else {
        assetId = selectedItem.assetId;
    }

    useEffect(() => {
        const getData = async () => {
            let items;
            if (assetId) {
                items = await fetchAllComments({ assetId: assetId });
            }

            if (items !== false && Array.isArray(items)) {
                setReload(false);
                setAllItems(
                    //@todo fix workflow delete return
                    items.filter((item) => item.assetId.indexOf("#deleted") === -1)
                );
            }
        };
        if (reload) {
            getData();
        }
    }, [reload, assetId, fetchAllComments]);

    var modules = {
        toolbar: [
            ["bold", "italic", "underline", "strike"],
            [, { list: "bullet" }],
            ["link"],
            ["clean"],
        ],
    };

    var formats = ["bold", "italic", "underline", "strike", "bullet", "link"];

    const addComment = (event) => {
        event.preventDefault();
        setValue("");
        const assetId = selectedItems[0].assetId;
        const versionId = selectedItems[0].currentVersion.S3Version;
        const uuid = "x" + generateUUID();
        const assetVersionIdAndCommentId = `${versionId}:${uuid}`;
        API.post(
            "api",
            `comments/assets/${assetId}/assetVersionId:commentId/${assetVersionIdAndCommentId}`,
            {
                body: {
                    // ...formState,
                    // acl: selectedOptions.map((option) => option.value),
                    commentBody: value,
                },
            }
        )
            .then((res) => {
                console.log("create comment", res);
            })
            .catch((err) => {
                console.log("create comment error", err);
            })
            .finally(() => {
                console.log("finally");
                setReload(true);
            });
    };

    var disableComments = selectedItems[0].assetId === undefined ? true : false;

    return (
        <div>
            <Box padding={{ top: "m", horizontal: "l" }}>
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
                                                        <TextFilter
                                                        // filteringText={filteringText}
                                                        // filteringPlaceholder="Find instances"
                                                        // filteringAriaLabel="Filter instances"
                                                        // onChange={({ detail }) =>
                                                        //     setFilteringText(detail.filteringText)
                                                        // }
                                                        />
                                                    </td>
                                                </tr>
                                                <tr className="commentSectionAssetsContainerTable">
                                                    <td>
                                                        <div className="commentSectionAssetsData">
                                                            <PopulateComments
                                                                selectedItems={selectedItems}
                                                                allComments={allItems}
                                                            />
                                                        </div>
                                                    </td>
                                                </tr>
                                                <tr>
                                                    <td className="commentSectionTableBorder">
                                                        <form onSubmit={addComment}>
                                                            <div className="container">
                                                                {/* Documentation: https://github.com/zenoamaro/react-quill */}
                                                                <div className="commentSectionTextBoxContainer">
                                                                    <ReactQuill
                                                                        theme="snow"
                                                                        value={value}
                                                                        onChange={setValue}
                                                                        id="commentInput"
                                                                        readOnly={disableComments}
                                                                        modules={modules}
                                                                        formats={formats}
                                                                    />
                                                                </div>
                                                                <div className="commentSectionSubmitButton">
                                                                    <Button
                                                                        variant="primary"
                                                                        disabled={disableComments}
                                                                    >
                                                                        Submit
                                                                    </Button>
                                                                </div>
                                                            </div>
                                                        </form>
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

// CommentsList.propTypes = {
//     singularName: PropTypes.string.isRequired,
//     singularNameTitleCase: PropTypes.string.isRequired,
//     pluralName: PropTypes.string.isRequired,
//     pluralNameTitleCase: PropTypes.string.isRequired,
//     listDefinition: PropTypes.instanceOf(ListDefinition).isRequired,
//     CreateNewElement: PropTypes.func,
//     fetchElements: PropTypes.func.isRequired,
//     fetchAllElements: PropTypes.func,
//     onCreateCallback: PropTypes.func,
//     isRelatedTable: PropTypes.bool,
//     editEnabled: PropTypes.bool,
// };
