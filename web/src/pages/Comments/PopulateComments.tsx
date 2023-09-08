/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import VersionComments from "./VersionComments";
import { CommentType, AssetType } from "./Comments";

export default function PopulateComments(props: any) {
    const { loading, showLoading, userId, selectedItems, allComments, setReload } = props;

    // sort the comments so that the most recent comment appears at the bottom of the list
    allComments.sort(function (a: CommentType, b: CommentType) {
        return new Date(a.dateCreated).valueOf() - new Date(b.dateCreated).valueOf();
    });

    const items = selectedItems.filter(
        (x: AssetType) => x.assetId !== undefined && x.assetId !== null
    );

    if (items.length > 0) {
        return (
            <div data-testid="expandableSectionDiv">
                {items.map((item: AssetType) => {
                    let allVersions;
                    if (item.versions === undefined) {
                        allVersions = [item.currentVersion];
                    } else {
                        // shallow copy of the item.versions array
                        allVersions = [...item.versions];
                        allVersions.push(item.currentVersion);
                    }
                    return allVersions.map((version, index) => {
                        // filter comments and only return comments for this versionId
                        const comments = allComments.filter(
                            (comment: CommentType) =>
                                comment["assetVersionId:commentId"].split(":")[0] ===
                                version.S3Version
                        );
                        return (
                            <VersionComments
                                key={`${version.S3Version}:${version.Version}`}
                                loading={loading}
                                showLoading={showLoading}
                                userId={userId}
                                setReload={setReload}
                                defaultExpanded={allVersions.length - 1 === index}
                                version={version}
                                comments={comments}
                            ></VersionComments>
                        );
                    });
                })}
            </div>
        );
    } else {
        return <div data-testid="expandableSectionDiv"></div>;
    }
}
