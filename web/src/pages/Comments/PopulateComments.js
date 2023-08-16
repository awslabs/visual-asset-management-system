/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import VersionComments from "./VersionComments";

export default function PopulateComments(props) {
    const { loading, showLoading, userId, selectedItems, allComments, setReload } = props;

    // sort the comments so that the most recent comment appears at the bottom of the list
    allComments.sort(function (a, b) {
        return new Date(a.dateCreated) - new Date(b.dateCreated);
    });

    return (
        <div data-testid="expandableSectionDiv">
            {selectedItems.map((item) => {
                if (item.assetId != null) {
                    let allVersions;
                    if (item.versions == undefined) {
                        allVersions = [item.currentVersion];
                    } else {
                        // shallow copy of the item.versions array
                        allVersions = [...item.versions];
                        allVersions.push(item.currentVersion);
                    }
                    return allVersions.map((version, index) => {
                        // filter comments and only return comments for this versionId
                        const comments = allComments.filter(
                            (comment) =>
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
                }
                return <></>;
            })}
        </div>
    );
}
