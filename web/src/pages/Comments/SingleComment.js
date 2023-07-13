import React, { useEffect, useState } from "react";
import { addColumnSortLabels } from "../../common/helpers/labels";
import moment from 'moment';
import Icon from "@cloudscape-design/components/icon";

export default function SingleComment(props) {
    const {
        commentBody,
        commentOwnerID,
        commentOwnerUsername,
        nestedCommentBool,
        dateCreated,
        dateEdited,
    } = props

    var timeDisplay = moment(dateCreated).fromNow();
    if (nestedCommentBool) {
        return (
            <>
                <div className="singleNestedCommentContainer">
                    {/* Dangerously setting innerHTML bc comments are already stored with HTML tags */}
                    <div className="singleNestedComment" dangerouslySetInnerHTML={{ __html: commentBody }}>
                    </div>
                    <div hidden={dateEdited === undefined} className="commentTimeAgoDiv">
                        <p><em>(edited)</em></p>
                    </div>
                </div>
            </>
        )
    }
    return (
        <>
            <div className="singleCommentContainer">
                <div className="pfpContainer">
                    <Icon name="user-profile" size="big" />
                </div>
                <div>
                    <p><b>{commentOwnerUsername}</b></p>
                </div>
                <div className="commentTimeAgoDiv">
                    <p>{timeDisplay}</p>
                </div>
            </div>
            <div className="container">
                {/* Dangerously setting innerHTML bc comments are already stored with HTML tags */}
                <div className="singleComment" dangerouslySetInnerHTML={{ __html: commentBody }}>
                </div>
                <div hidden={dateEdited === undefined} className="commentTimeAgoDiv">
                    <p><em>(edited)</em></p>
                </div>
            </div>
        </>
    )
}