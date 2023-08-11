/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState } from "react";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import SingleComment from "./SingleComment";
import LoadingIcons from "react-loading-icons";
import moment from "moment";

export default function VersionComments(props) {
    const { loading, showLoading, userId, setReload, defaultExpanded, version, comments } = props;

    const [localLoading, setLocalLoading] = useState("");

    if (localLoading && !loading) {
        setLocalLoading(false);
    }

    const reloadComments = (keepItemsDisplayed) => {
        setLocalLoading(keepItemsDisplayed);
        showLoading(keepItemsDisplayed);
    };

    let previousOwnerId = undefined;
    let previousCommentTime = undefined;
    let nestedCommentBool = false;

    return (
        <ExpandableSection
            defaultExpanded={defaultExpanded}
            key={version.S3Version}
            headerText={`V${version.Version}`}
            headerDescription={version.Comment}
        >
            {comments.map((comment) => {
                nestedCommentBool =
                    comment.commentOwnerID === previousOwnerId &&
                    moment(comment.dateCreated).diff(previousCommentTime, "minutes") < 10;
                previousOwnerId = comment.commentOwnerID;
                previousCommentTime = comment.dateCreated;
                const key = comment["assetVersionId:commentId"].split(":")[1];
                return (
                    <SingleComment
                        key={key}
                        userId={userId}
                        comment={comment}
                        setLocalLoading={setLocalLoading}
                        setReload={setReload}
                        reloadComments={reloadComments}
                        nestedCommentBool={nestedCommentBool}
                    />
                );
            })}
            <div className="center" hidden={!(loading && localLoading)}>
                <LoadingIcons.SpinningCircles className="center" fill="#000716" />
            </div>
        </ExpandableSection>
    );
}
