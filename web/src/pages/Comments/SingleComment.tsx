/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useRef } from "react";
import { API } from "aws-amplify";
import { Icon, Button, TextContent } from "@cloudscape-design/components";
import { deleteComment } from "../../services/APIService";
import moment from "moment";
import sanitizeHtml from "sanitize-html";
import JoditEditor from "jodit-react";

export default function SingleComment(props: any) {
    const { userId, comment, setReload, reloadComments, nestedCommentBool } = props;

    const [showOnHover, setShowOnHover] = useState({ display: "none" });
    const [editCommentBool, setEditCommentBool] = useState(false);
    const [deleteCommentBool, setDeleteCommentBool] = useState(false);
    const [content, setContent] = useState("");
    const editor = useRef(null);

    const STAY_HIDDEN = { display: "none" };

    const config = {
        readonly: false,
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

    var editCommentIconsVisible = false;
    if (userId === comment.commentOwnerID && !editCommentBool && !deleteCommentBool) {
        editCommentIconsVisible = true;
    }

    var timeDisplay = moment(comment.dateCreated).fromNow();

    const selectEditComment = () => {
        editCommentIconsVisible = false;
        setEditCommentBool(true);
        setContent(comment.commentBody);
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
        if (content === comment.commentBody) {
            reloadComments(true);
            setReload(true);
            setContent("");
            return;
        }
        setContent("");
        reloadComments(true);
        try {
            let response = await API.put(
                "api",
                `comments/assets/${comment.assetId}/assetVersionId:commentId/${comment["assetVersionId:commentId"]}`,
                {
                    body: {
                        commentBody: content,
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
                    if (!editCommentBool) {
                        setShowOnHover({ display: "block" });
                    }
                }}
                onMouseLeave={(e) => {
                    if (!editCommentBool) {
                        setShowOnHover({ display: "none" });
                    }
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
                            <Button iconName="edit" variant="icon" onClick={selectEditComment} />
                            <Button
                                iconName="remove"
                                variant="icon"
                                onClick={selectDeleteComment}
                            />
                        </div>
                        <div style={editCommentBool ? { display: "block" } : { display: "none" }}>
                            <Button iconName="check" variant="icon" onClick={confirmEdit} />
                            <Button iconName="close" variant="icon" onClick={cancelEdit} />
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
                            data-testid="singleComment"
                            dangerouslySetInnerHTML={{ __html: sanitizeHtml(comment.commentBody) }}
                        ></div>
                    </TextContent>
                    <div hidden={!editCommentBool} className="quillEditDiv">
                        <JoditEditor
                            ref={editor}
                            value={content}
                            config={config}
                            onBlur={handleUpdate}
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
                        if (!editCommentBool) {
                            setShowOnHover({ display: "block" });
                        }
                    }}
                    onMouseLeave={(e) => {
                        if (!editCommentBool) {
                            setShowOnHover({ display: "none" });
                        }
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
                        <JoditEditor
                            ref={editor}
                            value={content}
                            config={config}
                            onBlur={handleUpdate}
                            onChange={(newContent) => {}}
                        />
                        <div style={editCommentBool ? { display: "block" } : { display: "none" }}>
                            <Button iconName="check" variant="icon" onClick={confirmEdit} />
                            <Button iconName="close" variant="icon" onClick={cancelEdit} />
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
                        <Button iconName="edit" variant="icon" onClick={selectEditComment} />
                        <Button iconName="remove" variant="icon" onClick={selectDeleteComment} />
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
