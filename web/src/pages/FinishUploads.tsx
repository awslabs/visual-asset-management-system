import { Box, Grid, SpaceBetween, TextContent } from "@cloudscape-design/components";
import Header from "@cloudscape-design/components/header";
import React, { useContext, useEffect, useRef, useState } from "react";
import { AssetDetail } from "./AssetUpload";
import { useLocation, useParams } from "react-router";
import localforage from "localforage";
import { FileUploadTable, FileUploadTableItem } from "./AssetUpload/FileUploadTable";
import { createAssetUploadPromises, executeUploads } from "./AssetUpload/onSubmit";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import Synonyms from "../synonyms";
import { FileInfo, MultiFileSelect } from "../components/multifile/MultiFileSelect";
import { AssetDetailContext, AssetDetailContextType } from "../context/AssetDetailContext";
import { Link } from "@cloudscape-design/components";

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

interface FinishUploadsProps {
    assetDetailState: AssetDetail;
    keyPrefix: string;
    isNewFiles: boolean;
    // uploadItems: FileUploadTableItem[]
}

const FinishUploads = ({ assetDetailState, keyPrefix, isNewFiles = false }: FinishUploadsProps) => {
    const [reuploadClicked, setReuploadClicked] = useState(false);
    const [assetDetail, setAssetDetail] = useState(assetDetailState);

    const get_completed_items = (items: FileUploadTableItem[]) => {
        return items.filter((item) => item.status === "Completed");
    };

    useEffect(() => {
        setAssetDetail(assetDetailState);
    }, [assetDetailState]);

    useEffect(() => {
        if (assetDetail && assetDetail.assetId) {
            localforage
                .setItem(assetDetail.assetId!, assetDetail)
                .then(() => {
                    // console.log("local asset saved", assetDetail)
                })
                .catch((error) => {});
        }
    }, [assetDetail]);

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
        setAssetDetail((prevAssetDetail: AssetDetail) => {
            return {
                ...prevAssetDetail,
                //@ts-ignore
                Asset: prevAssetDetail.Asset.map((item) =>
                    item.index === index ? getUpdatedItemAfterProgress(item, loaded, total) : item
                ),
            };
        });
    };

    const fileUploadComplete = (index: number, event: any) => {
        setAssetDetail((prevAssetDetail: AssetDetail) => {
            return {
                ...prevAssetDetail,
                //@ts-ignore
                Asset: prevAssetDetail.Asset.map((item) =>
                    item.index === index ? { ...item, status: "Completed", progress: 100 } : item
                ),
            };
        });
    };

    const fileUploadError = (index: number, event: any) => {
        setAssetDetail((prevAssetDetail: AssetDetail) => {
            return {
                ...prevAssetDetail,
                //@ts-ignore
                Asset: prevAssetDetail.Asset.map((item) =>
                    item.index === index ? { ...item, status: "Failed" } : item
                ),
            };
        });
    };

    const moveToQueued = (index: number) => {
        setAssetDetail((prevAssetDetail: AssetDetail) => {
            return {
                ...prevAssetDetail,
                //@ts-ignore
                Asset: prevAssetDetail.Asset.map((item) =>
                    item.index === index ? { ...item, status: "Queued" } : item
                ),
            };
        });
    };

    function getBasePathPrefix(filePath: string): string {
        // Ensure path ends with a trailing slash for consistent handling
        const normalizedPath = filePath.endsWith("/") ? filePath : filePath + "/";
        // Find the last index of the path separator (/)
        const lastIndex = normalizedPath.lastIndexOf("/");
        // Extract the base folder path, ensuring a trailing slash
        const basePath = normalizedPath.slice(0, lastIndex + 1);
        return basePath;
    }

    const onUpload = () => {
        console.log("Calling on Upload Try");
        // console.log("KeyPrefix:" + assetDetail.key === "/" ? assetDetail.key : isNewFiles ? getBasePathPrefix(assetDetail.key!) : assetDetail.key)
        console.log(assetDetail);

        if (
            // result &&
            assetDetail &&
            assetDetail.key &&
            assetDetail.assetId &&
            assetDetail.databaseId &&
            assetDetail.Asset
        ) {
            setReuploadClicked(true);
            const uploads = createAssetUploadPromises(
                assetDetail.isMultiFile,
                assetDetail.Asset,
                assetDetail.key === "/"
                    ? assetDetail.key
                    : isNewFiles
                    ? getBasePathPrefix(assetDetail.key)
                    : assetDetail.key,
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
            executeUploads(uploads)
                .then(() => {})
                .catch((err: any) => {
                    return Promise.reject(err);
                });
        }
        //});
    };

    return (
        <>
            {assetDetail?.Asset && (
                <SpaceBetween direction="vertical" size="l">
                    <Box variant="awsui-key-label">
                        Upload Progress for {Synonyms.Asset}
                        <Link
                            href={`#/databases/${assetDetail.databaseId}/assets/${assetDetail.assetId}`}
                            target="_blank"
                        >
                            {` ${assetDetail.assetName}`}
                        </Link>
                    </Box>
                    <ProgressBar
                        status={
                            get_completed_items(assetDetail.Asset).length ===
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
                        onRetry={onUpload}
                        resume={!reuploadClicked}
                        showCount={true}
                    />
                </SpaceBetween>
            )}
        </>
    );
};

const convertToFileUploadTableItems = (fileInfo: FileInfo[]): FileUploadTableItem[] => {
    return fileInfo.map((file, index) => {
        return {
            index: index,
            name: file.path,
            size: 0,
            status: "Queued",
            progress: 0,
            loaded: 0,
            total: 0,
            startedAt: 0,
            handle: file.handle,
            relativePath: file.path,
        };
    });
};

export default function FinishUploadsPage() {
    const { state } = useLocation();
    // const { assetDetailState } = state
    const [assetDetail, setAssetDetail] = useState(state.assetDetailState);
    console.log(state);

    return (
        <Box padding={{ top: false ? "s" : "m", horizontal: "l" }}>
            <SpaceBetween size={"s"} direction={"vertical"}>
                <Grid gridDefinition={[{ colspan: { default: 12 } }]}>
                    <div>
                        <TextContent>
                            <Header variant="h1"> Pending Uploads </Header>
                        </TextContent>
                        <FinishUploads
                            assetDetailState={assetDetail}
                            keyPrefix={state.fileTree.keyPrefix}
                            isNewFiles={state.isNewFiles ? state.isNewFiles : false}
                        />
                    </div>
                </Grid>
                <h1> Add more files </h1>
                <MultiFileSelect
                    onChange={(fileSelection) => {
                        const selectedItems = convertToFileUploadTableItems(fileSelection);
                        // console.log(selectedItems)
                        const uploadItems = selectedItems; //mergeItems(assetDetail.Asset, selectedItems)
                        // console.log(uploadItems)

                        //@ts-ignore
                        setAssetDetail((assetDetail) => {
                            // console.log(assetDetail)
                            // console.log(uploadItems)
                            return {
                                ...assetDetail,
                                Asset: uploadItems,
                                isMultiFile: assetDetail?.isMultiFile || uploadItems.length > 0,
                            };
                        });
                    }}
                />
            </SpaceBetween>
        </Box>
    );
}
