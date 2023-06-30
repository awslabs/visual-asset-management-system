import { Box, Grid, Link, SpaceBetween, TextContent } from "@cloudscape-design/components";
import Header from "@cloudscape-design/components/header";
import React, { useEffect, useState } from "react";
import { AssetDetail } from "./AssetUpload";
import { useParams } from "react-router";
import localforage from "localforage";
import { FileUploadTable, FileUploadTableItem } from "./AssetUpload/FileUploadTable";
import { createAssetUploadPromises, executeUploads } from "./AssetUpload/onSubmit";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import Synonyms from "../synonyms";

export async function verifyPermission(fileHandle: any, readWrite: any) {
    const options = {};
    if (readWrite) {
        //@ts-ignore
        options.mode = "readwrite";
    }
    // Check if permission was already granted. If so, return true.
    if ((await fileHandle.queryPermission(options)) === "granted") {
        return true;
    }
    // Request permission. If the user grants permission, return true.
    if ((await fileHandle.requestPermission(options)) === "granted") {
        return true;
    }
    // The user didn't grant permission, so return false.
    return false;
}

const FinishUploads = () => {
    const [assetDetail, setAssetDetail] = useState<AssetDetail | null>(null);
    const [fileUploadTableItems, setFileUploadTableItems] = useState<FileUploadTableItem[]>([]);
    const [reuploadClicked, setReuploadClicked] = useState(false);
    const { assetId } = useParams();

    const get_completed_items = (items: FileUploadTableItem[]) => {
        return items.filter((item) => item.status === "Completed");
    };

    useEffect(() => {
        if (assetId) {
            localforage.getItem<AssetDetail>(assetId).then((assetDetail) => {
                setAssetDetail(assetDetail);
                if (assetDetail?.Asset) {
                    setFileUploadTableItems(assetDetail.Asset);
                }
                console.log("local asset found");
            });
        }
    }, [assetId]);
    useEffect(() => {
        if (assetDetail && assetDetail.assetId && fileUploadTableItems.length > 0) {
            //@ts-ignore
            setAssetDetail((assetDetail) => {
                return { ...assetDetail, Asset: fileUploadTableItems };
            });
            localforage
                .setItem(assetDetail.assetId, { ...assetDetail, Asset: fileUploadTableItems })
                .then(() => {})
                .catch((error) => {});
        }
    }, [fileUploadTableItems]);

    const getUpdatedItemAfterProgress = (
        item: FileUploadTableItem,
        loaded: number,
        total: number
    ): FileUploadTableItem => {
        const progress = Math.round((loaded / total) * 100);
        const status = item.status;
        if (loaded === total) {
            return {
                ...item,
                loaded: loaded,
                total: total,
                progress: progress,
                status: "Completed",
            };
        }
        if (status === "Queued") {
            return {
                ...item,
                loaded: loaded,
                total: total,
                progress: progress,
                status: "In Progress",
                startedAt: Math.floor(new Date().getTime() / 1000),
            };
        } else {
            return {
                ...item,
                loaded: loaded,
                total: total,
                status: "In Progress",
                progress: progress,
            };
        }
    };
    const updateProgressForFileUploadItem = (index: number, loaded: number, total: number) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? getUpdatedItemAfterProgress(item, loaded, total) : item
            );
        });
    };

    const fileUploadComplete = (index: number, event: any) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? { ...item, status: "Completed", progress: 100 } : item
            );
        });
    };

    const fileUploadError = (index: number, event: any) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? { ...item, status: "Failed" } : item
            );
        });
    };

    const moveToQueued = (index: number) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? { ...item, status: "Queued" } : item
            );
        });
    };

    const onRetry = () => {
        console.log("Calling on retry");
        let handle;
        if (assetDetail && assetDetail.Asset && assetDetail.Asset.length === 1) {
            handle = assetDetail.Asset[0].handle;
        } else {
            handle = assetDetail?.DirectoryHandle;
        }
        verifyPermission(handle, true).then((result: boolean) => {
            if (
                result &&
                assetDetail &&
                assetDetail.key &&
                assetDetail.assetId &&
                assetDetail.databaseId &&
                fileUploadTableItems
            ) {
                setReuploadClicked(true);
                const uploads = createAssetUploadPromises(
                    assetDetail.isMultiFile,
                    fileUploadTableItems,
                    assetDetail.key,
                    {
                        assetId: assetDetail.assetId,
                        databaseId: assetDetail.databaseId,
                    },
                    moveToQueued,
                    (index: number, progress: any) => {
                        updateProgressForFileUploadItem(index, progress.loaded, progress.total);
                    },
                    (index: number, event: any) => {
                        console.log("Completed Upload");
                        fileUploadComplete(index, event);
                    },
                    (index: number, event: any) => {
                        console.log("Error Uploading", event);
                        fileUploadError(index, event);
                    }
                );
                executeUploads(uploads).catch((err: any) => {
                    return Promise.reject(err);
                });
            }
        });
    };
    return (
        <>
            {assetDetail?.Asset && (
                <SpaceBetween direction="vertical" size="l">
                    <Box variant="awsui-key-label">
                        Upload Progress for {Synonyms.Asset}
                        <Link
                            href={`/databases/${assetDetail.databaseId}/assets/${assetDetail.assetId}`}
                            target="_blank"
                        >
                            {` ${assetDetail.assetName}`}
                        </Link>
                    </Box>
                    <ProgressBar
                        status={
                            get_completed_items(assetDetail.Asset).length ==
                            assetDetail.Asset.length
                                ? "success"
                                : "in-progress"
                        }
                        value={
                            (get_completed_items(assetDetail.Asset).length /
                                assetDetail.Asset.length) *
                            100
                        }
                        label="Overall Upload Progress"
                    />
                    <FileUploadTable
                        allItems={assetDetail?.Asset}
                        onRetry={onRetry}
                        resume={!reuploadClicked}
                        showCount={true}
                    />
                </SpaceBetween>
            )}
        </>
    );
};

export default function FinishUploadsPage() {
    return (
        <Box padding={{ top: false ? "s" : "m", horizontal: "l" }}>
            <Grid gridDefinition={[{ colspan: { default: 12 } }]}>
                <div>
                    <TextContent>
                        <Header variant="h1">Finish Pending Uploads </Header>
                    </TextContent>

                    <FinishUploads />
                </div>
            </Grid>
        </Box>
    );
}
