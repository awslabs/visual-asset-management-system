import {Box, Grid, Link, SpaceBetween, TextContent} from "@cloudscape-design/components";
import Header from "@cloudscape-design/components/header";
import React, {useEffect, useRef, useState} from "react";
import {AssetDetail} from "./AssetUpload";
import {useLocation, useParams} from "react-router";
import localforage from "localforage";
import {FileUploadTable, FileUploadTableItem} from "./AssetUpload/FileUploadTable";
import {createAssetUploadPromises, executeUploads} from "./AssetUpload/onSubmit";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import Synonyms from "../synonyms";
import {FileInfo, MultiFileSelect} from "../components/multifile/MultiFileSelect";

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
    asset: AssetDetail
    uploadItems: FileUploadTableItem[]
}

const mergeItems = (items: FileUploadTableItem[], newItems: FileUploadTableItem[]): FileUploadTableItem[] => {
    if (!items) {
        return newItems;
    } else if (!newItems) {
        return items;
    } else {
        const mergedItems = items.map((item) => {
            const newItem = newItems.find((newItem) => newItem.relativePath === item.relativePath);
            if (newItem) {
                return {
                    ...item,
                    ...newItem,
                };
            }
            return item;
        });
        return mergedItems;
    }
}

const FinishUploads = ({asset, uploadItems}: FinishUploadsProps) => {
    const [assetDetail, setAssetDetail] = useState<AssetDetail>(asset);
    const [fileUploadTableItems, setFileUploadTableItems] = useState<FileUploadTableItem[]>(mergeItems(asset.Asset || [], uploadItems || []));
    const [reuploadClicked, setReuploadClicked] = useState(false);
    const {assetId} = useParams();

    const get_completed_items = (items: FileUploadTableItem[]) => {
        return items.filter((item) => item.status === "Completed");
    };

    useEffect(() => {

        if (assetDetail && assetDetail.assetId) {
            const updatedItems = mergeItems(assetDetail.Asset || [], uploadItems || []);
            setFileUploadTableItems(updatedItems);
            //@ts-ignore
            setAssetDetail((assetDetail) => {
                return {
                    ...assetDetail,
                    Asset: uploadItems,
                    isMultiFile: assetDetail?.isMultiFile || updatedItems.length > 0
                };
            });
            localforage
                .setItem(assetDetail.assetId, {...assetDetail, Asset: updatedItems})
                .then(() => {
                    console.log("local asset saved", assetDetail)
                })
                .catch((error) => {
                });
        }
    }, [uploadItems]);

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
                item.index === index ? {...item, status: "Completed", progress: 100} : item
            );
        });
    };

    const fileUploadError = (index: number, event: any) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? {...item, status: "Failed"} : item
            );
        });
    };

    const moveToQueued = (index: number) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? {...item, status: "Queued"} : item
            );
        });
    };

    const onRetry = () => {
        console.log("Calling on retry");

            if (
                // result &&
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
        //});
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
            relativePath: file.path
        };
    });
}

export default function FinishUploadsPage() {
    const {state} = useLocation()
    const [fileUploadTableItems, setFileUploadTableItems] = useState<FileUploadTableItem[]>([]);
    return (
        <Box padding={{top: false ? "s" : "m", horizontal: "l"}}>
            <SpaceBetween size={"s"} direction={"vertical"}>
                <Grid gridDefinition={[{colspan: {default: 12}}]}>
                    <div>
                        <TextContent>
                            <Header variant="h1"> Pending Uploads </Header>
                        </TextContent>
                        <FinishUploads uploadItems={fileUploadTableItems} asset={state.assetDetail}/>
                    </div>
                </Grid>
                <h1> Add more files </h1>
                <MultiFileSelect onChange={(fileSelection) => {
                    setFileUploadTableItems(convertToFileUploadTableItems(fileSelection))
                }}/>
            </SpaceBetween>
        </Box>
    );
}
