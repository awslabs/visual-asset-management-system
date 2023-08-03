/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from "react";
import { API } from "aws-amplify";
import { Icon, Button, TextContent } from "@cloudscape-design/components";
import { deleteComment } from "../../services/APIService";
import moment from "moment";
import sanitizeHtml from "sanitize-html";
import ReactQuill from "react-quill";
import "react-quill/dist/quill.snow.css";

export default function SingleComment(props) {
    const { userId, comment, setReload, reloadComments, nestedCommentBool } = props;

    const [value, setValue] = useState("");
    const [showOnHover, setShowOnHover] = useState({ display: "none" });
    const [editCommentBool, setEditCommentBool] = useState(false);
    const [deleteCommentBool, setDeleteCommentBool] = useState(false);

    const STAY_HIDDEN = { display: "none" };

    var editCommentIconsVisible = false;
    if (userId === comment.commentOwnerID && !editCommentBool && !deleteCommentBool) {
        editCommentIconsVisible = true;
    }

    var timeDisplay = moment(comment.dateCreated).fromNow();

    let modules = {
        toolbar: [
            ["bold", "italic", "underline", "strike"],
            [{ list: "ordered" }, { list: "bullet" }],
            ["link"],
            ["clean"],
        ],
    };

    let formats = ["bold", "italic", "underline", "strike", "list", "bullet", "link"];

    const selectEditComment = () => {
        editCommentIconsVisible = false;
        setEditCommentBool(true);
        setValue(comment.commentBody);
    };

    const confirmEdit = () => {
        postCommentEdit();
        setEditCommentBool(false);
    };

    const cancelEdit = () => {
        setEditCommentBool(false);
    };

    const selectDeleteComment = () => {
        editCommentIconsVisible = false;
        setDeleteCommentBool(true);
    };

    const confirmDelete = async () => {
        reloadComments(true);
        setDeleteCommentBool(false);
        await deleteComment({
            assetId: comment.assetId,
            assetVersionIdAndCommentId: comment["assetVersionId:commentId"],
        });
        setReload(true);
    };

    const cancelDelete = () => {
        setDeleteCommentBool(false);
    };

    const postCommentEdit = async () => {
        if (value === comment.commentBody) {
            reloadComments(true);
            setReload(true);
            setValue("");
            return;
        }
        setValue("");
        reloadComments(true);
        try {
            let response = await API.put(
                "api",
                `comments/assets/${comment.assetId}/assetVersionId:commentId/${comment["assetVersionId:commentId"]}`,
                {
                    body: {
                        commentBody: value,
                    },
                }
            );
            console.log(response);
        } catch (e) {
            console.log("edit comment error");
            console.log(e);
        } finally {
            setReload(true);
        }
    };

    // return a full comment with its own header of username and profile pic
    if (nestedCommentBool === false) {
        return (
            <div
                data-testid="fullCommentDiv"
                onMouseEnter={(e) => {
                    setShowOnHover({ display: "block" });
                }}
                onMouseLeave={(e) => {
                    setShowOnHover({ display: "none" });
                }}
            >
                <div className="singleCommentContainer">
                    <div className="profilePicContainer">
                        <Icon name="user-profile" size="big" />
                    </div>
                    <div className="usernameTextContainer">
                        <TextContent>
                            <p>
                                <b>{comment.commentOwnerUsername}</b>
                            </p>
                        </TextContent>
                    </div>
                    <div className="commentTimeAgoDiv">
                        <TextContent>
                            <small>{timeDisplay}</small>
                        </TextContent>
                    </div>
                    <div hidden={comment.dateEdited === undefined} className="commentTimeAgoDiv">
                        <TextContent>
                            <small>
                                <em>(edited)</em>
                            </small>
                        </TextContent>
                    </div>
                    <div className="singleCommentIcons container">
                        <div
                            data-testid="editDeleteCommentDiv"
                            style={editCommentIconsVisible ? showOnHover : STAY_HIDDEN}
                        >
                            <Button
                                iconName="edit"
                                size="small"
                                variant="icon"
                                onClick={selectEditComment}
                            />
                            <Button
                                iconName="remove"
                                size="small"
                                variant="icon"
                                onClick={selectDeleteComment}
                            />
                        </div>
                        <div style={editCommentBool ? { display: "block" } : { display: "none" }}>
                            <Button
                                iconName="check"
                                size="small"
                                variant="icon"
                                onClick={confirmEdit}
                            />
                            <Button
                                iconName="close"
                                size="small"
                                variant="icon"
                                onClick={cancelEdit}
                            />
                        </div>
                        <div style={deleteCommentBool ? { display: "block" } : { display: "none" }}>
                            <button className="deleteCommentButtons" onClick={confirmDelete}>
                                Confirm Delete
                            </button>
                            <button className="deleteCommentButtons" onClick={cancelDelete}>
                                Cancel Delete
                            </button>
                        </div>
                    </div>
                </div>
                <div className="container">
                    {/* Dangerously setting innerHTML bc comments are already stored with HTML tags */}
                    <TextContent>
                        <div
                            hidden={editCommentBool}
                            className={
                                "singleComment" + (deleteCommentBool ? " deleteCommentBorder" : "")
                            }
                            dangerouslySetInnerHTML={{ __html: sanitizeHtml(comment.commentBody) }}
                        ></div>
                    </TextContent>
                    <div hidden={!editCommentBool} className="quillEditDiv">
                        <ReactQuill
                            theme="snow"
                            value={value}
                            onChange={setValue}
                            modules={modules}
                            formats={formats}
                        />
                    </div>
                </div>
            </div>
        );
    } else {
        // return a sub-comment to nest under another comment
        return (
            <>
                <div
                    data-testid="nestedFullCommentDiv"
                    className="singleNestedCommentContainer"
                    onMouseEnter={(e) => {
                        setShowOnHover({ display: "block" });
                    }}
                    onMouseLeave={(e) => {
                        setShowOnHover({ display: "none" });
                    }}
                >
                    {/* Dangerously setting innerHTML bc comments are already stored with HTML tags */}
                    <TextContent>
                        <div
                            className={
                                "singleNestedComment" +
                                (deleteCommentBool ? " deleteCommentBorder" : "")
                            }
                            hidden={editCommentBool}
                            dangerouslySetInnerHTML={{ __html: sanitizeHtml(comment.commentBody) }}
                        ></div>
                    </TextContent>
                    <div hidden={!editCommentBool} className="quillEditDiv">
                        <ReactQuill
                            theme="snow"
                            value={value}
                            onChange={setValue}
                            modules={modules}
                            formats={formats}
                        />
                        <div style={editCommentBool ? { display: "block" } : { display: "none" }}>
                            <Button
                                iconName="check"
                                size="small"
                                variant="icon"
                                onClick={confirmEdit}
                            />
                            <Button
                                iconName="close"
                                size="small"
                                variant="icon"
                                onClick={cancelEdit}
                            />
                        </div>
                    </div>
                    <div
                        hidden={comment.dateEdited === undefined}
                        className="commentTimeAgoDiv nestedEditedDiv"
                    >
                        <TextContent>
                            <small>
                                <em>(edited)</em>
                            </small>
                        </TextContent>
                    </div>
                    <div
                        data-testid="editDeleteNestedCommentDiv"
                        className="singleNestedCommentIcons"
                        style={editCommentIconsVisible ? showOnHover : STAY_HIDDEN}
                    >
                        <Button
                            iconName="edit"
                            size="small"
                            variant="icon"
                            onClick={selectEditComment}
                        />
                        <Button
                            iconName="remove"
                            size="small"
                            variant="icon"
                            onClick={selectDeleteComment}
                        />
                    </div>
                </div>
                <div
                    className="deleteCommentButtonsDiv"
                    style={deleteCommentBool ? { display: "block" } : { display: "none" }}
                >
                    <button className="deleteCommentButtons" onClick={confirmDelete}>
                        Confirm Delete
                    </button>
                    <button className="deleteCommentButtons" onClick={cancelDelete}>
                        Cancel Delete
                    </button>
                </div>
            </>
        );
    }
}
