import React, { useEffect, useState } from "react";

import { ExpandableSection } from "@cloudscape-design/components";
import "react-quill/dist/quill.snow.css";
import SingleComment from "./SingleComment";
import moment from "moment";

export default function PopulateComments(props) {
    const { selectedItems, allComments } = props;

    // sort the comments so that the most recent comment appears at the bottom of the list
    allComments.sort(function (a, b) {
        return new Date(a.dateCreated) - new Date(b.dateCreated);
    });

    var previousOwnerId = undefined;
    var previousCommentTime = undefined;
    var nestedCommentBool = false;
    return (
        <>
            {selectedItems.map((item) => {
                if (item.assetId == null) {
                    return;
                }
                return (
                    <ExpandableSection
                        defaultExpanded
                        key={item.assetId}
                        headerText={item.assetName}
                    >
                        {allComments.map((comment) => {
                            nestedCommentBool =
                                comment.commentOwnerID === previousOwnerId &&
                                moment(comment.dateCreated).diff(previousCommentTime, "minutes") <
                                    10;
                            previousOwnerId = comment.commentOwnerID;
                            previousCommentTime = comment.dateCreated;
                            const key = comment["assetVersionId:commentId"].split(":")[1];
                            return (
                                <SingleComment
                                    key={key}
                                    commentBody={comment.commentBody}
                                    commentOwnerID={comment.commentOwnerID}
                                    nestedCommentBool={nestedCommentBool}
                                    commentOwnerUsername={comment.commentOwnerUsername}
                                    dateCreated={comment.dateCreated}
                                    dateEdited={comment.dateEdited}
                                />
                            );
                        })}
                    </ExpandableSection>
                );
            })}
        </>
    );
}
