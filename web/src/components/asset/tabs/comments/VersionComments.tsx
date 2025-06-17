/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useRef } from "react";
import { API } from "aws-amplify";
import {
  ExpandableSection,
  Button,
  SpaceBetween,
  Box,
  Modal,
  TextContent,
} from "@cloudscape-design/components";
import JoditEditor from "jodit-react";

interface CommentType {
  assetId: string;
  "assetVersionId:commentId": string;
  commentBody: string;
  commentOwnerID: string;
  commentOwnerUsername: string;
  dateCreated: string;
  dateEdited?: string;
}

interface VersionCommentsProps {
  loading: boolean;
  showLoading: (keepItemsDisplayed: boolean) => void;
  userId: string;
  setReload: (reload: boolean) => void;
  defaultExpanded: boolean;
  version: {
    Comment: string;
    dateModified: string;
    S3Version: string;
    Version: string;
    description: string;
  };
  comments: CommentType[];
}

export default function VersionComments(props: VersionCommentsProps) {
  const { loading, showLoading, userId, setReload, defaultExpanded, version, comments } = props;
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [visible, setVisible] = useState(false);
  const [editVisible, setEditVisible] = useState(false);
  const [commentToDelete, setCommentToDelete] = useState<string>("");
  const [commentToEdit, setCommentToEdit] = useState<CommentType | null>(null);
  const [editedContent, setEditedContent] = useState<string>("");
  const editor = useRef(null);

  // Config for the Jodit editor
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
    setEditedContent(event);
  };

  const handleDeleteComment = async () => {
    if (!commentToDelete) return;

    const [assetId, assetVersionIdAndCommentId] = commentToDelete.split(":");
    showLoading(true);

    try {
      await API.del(
        "api",
        `comments/assets/${assetId}/assetVersionId:commentId/${assetVersionIdAndCommentId}`,
        {}
      );
      setVisible(false);
      setReload(true);
    } catch (e: any) {
      console.log("delete comment error");
      if (e.response && e.response.status === 403) {
        localStorage.setItem("status", "403");
      }
      setVisible(false);
      setReload(true);
    }
  };

  const handleEditComment = async () => {
    if (!commentToEdit) return;

    const [assetId, assetVersionIdAndCommentId] = commentToEdit["assetVersionId:commentId"].split(":");
    showLoading(true);

    try {
      await API.put(
        "api",
        `comments/assets/${assetId}/assetVersionId:commentId/${commentToEdit["assetVersionId:commentId"]}`,
        {
          body: {
            commentBody: editedContent,
          },
        }
      );
      setEditVisible(false);
      setReload(true);
    } catch (e: any) {
      console.log("edit comment error");
      if (e.response && e.response.status === 403) {
        localStorage.setItem("status", "403post");
      }
      setEditVisible(false);
      setReload(true);
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleString();
  };

  return (
    <div>
      <ExpandableSection
        variant="container"
        header={`Version ${version.Version}`}
        expanded={expanded}
        onChange={({ detail }) => setExpanded(detail.expanded)}
      >
        <div>
          {comments.length === 0 ? (
            <div className="noCommentsDiv">No comments for this version</div>
          ) : (
            comments.map((comment) => (
              <div
                key={comment["assetVersionId:commentId"]}
                className="commentContainer"
              >
                <div className="commentHeader">
                  <div className="commentOwner">
                    <strong>{comment.commentOwnerUsername}</strong>
                  </div>
                  <div className="commentDate">
                    {formatDate(comment.dateCreated)}
                    {comment.dateEdited && (
                      <span> (edited: {formatDate(comment.dateEdited)})</span>
                    )}
                  </div>
                </div>
                <div
                  className="commentBody"
                  dangerouslySetInnerHTML={{ __html: comment.commentBody }}
                ></div>
                {userId === comment.commentOwnerUsername && (
                  <div className="commentActions">
                    <SpaceBetween direction="horizontal" size="xs">
                      <Button
                        variant="link"
                        onClick={() => {
                          setCommentToEdit(comment);
                          setEditedContent(comment.commentBody);
                          setEditVisible(true);
                        }}
                      >
                        Edit
                      </Button>
                      <Button
                        variant="link"
                        onClick={() => {
                          setCommentToDelete(
                            `${comment.assetId}:${comment["assetVersionId:commentId"]}`
                          );
                          setVisible(true);
                        }}
                      >
                        Delete
                      </Button>
                    </SpaceBetween>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </ExpandableSection>

      {/* Delete Confirmation Modal */}
      <Modal
        visible={visible}
        onDismiss={() => setVisible(false)}
        header="Delete comment"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setVisible(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleDeleteComment}>
                Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <TextContent>
          <p>Are you sure you want to delete this comment?</p>
        </TextContent>
      </Modal>

      {/* Edit Comment Modal */}
      <Modal
        visible={editVisible}
        onDismiss={() => setEditVisible(false)}
        header="Edit comment"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setEditVisible(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleEditComment}>
                Save
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <JoditEditor
          ref={editor}
          value={editedContent}
          config={config}
          onBlur={handleUpdate}
          onChange={(newContent) => {}}
        />
      </Modal>
    </div>
  );
}
