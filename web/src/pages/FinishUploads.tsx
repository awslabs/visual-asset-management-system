import {Box, Grid, TextContent} from "@cloudscape-design/components";
import Header from "@cloudscape-design/components/header";
import {useEffect, useState} from "react";
import {AssetDetail} from "./AssetUpload";
import {useParams} from "react-router";
import localforage from "localforage";
import {FileUploadTable, FileUploadTableItem} from "./AssetUpload/FileUploadTable";
import {createAssetUploadPromises, executeUploads} from "./AssetUpload/onSubmit";



export async function verifyPermission(fileHandle: any, readWrite: any) {
    const options = {};
    if (readWrite) {
        //@ts-ignore
        options.mode = 'readwrite';
    }
    // Check if permission was already granted. If so, return true.
    if ((await fileHandle.queryPermission(options)) === 'granted') {
        return true;
    }
    // Request permission. If the user grants permission, return true.
    if ((await fileHandle.requestPermission(options)) === 'granted') {
        return true;
    }
    // The user didn't grant permission, so return false.
    return false;
}

const FinishUploads = () => {
    const [assetDetail, setAssetDetail] = useState<AssetDetail | null>(null)
    const [fileUploadTableItems, setFileUploadTableItems] = useState<FileUploadTableItem[]>([]);
    const [reuploadClicked, setReuploadClicked] = useState(false);
    const {databaseId, assetId} = useParams()
    useEffect(() => {
        if (assetId) {
            localforage.getItem<AssetDetail>(assetId).then((assetDetail) => {
                setAssetDetail(assetDetail)
                if(assetDetail?.Asset) {
                    setFileUploadTableItems(assetDetail.Asset)
                }
                console.log("local asset found")
            })
        }
    }, [assetId])
    useEffect(() => {
        if (assetDetail && assetDetail.assetId && fileUploadTableItems.length > 0) {
            //@ts-ignore
            setAssetDetail((assetDetail) => {
                return {...assetDetail, Asset: fileUploadTableItems}
            });
            localforage.setItem(assetDetail.assetId, {...assetDetail, Asset: fileUploadTableItems}).then(() => {
                console.log("Asset saved to local storage")
            }).catch((error) => {
                console.log("Error saving asset to local storage")
            });
        }
    }, [fileUploadTableItems])

    const getUpdatedItemAfterProgress = (item: FileUploadTableItem, loaded: number, total: number): FileUploadTableItem =>  {
        console.log(item)
        const progress = Math.round((loaded / total) * 100)
        const status = item.status
        if(loaded === total) {
            return {...item, loaded: loaded, total: total, progress: progress, status: "Completed"}
        }
        if (status === 'Queued') {
            return {...item, loaded: loaded, total: total, progress: progress, status: "In Progress", startedAt: Math.floor((new Date()).getTime() / 1000)}
        } else {
            return {...item, loaded: loaded, total: total, status: "In Progress", progress: progress}
        }
    }
    const updateProgressForFileUploadItem = (index: number, loaded: number, total: number) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) => item.index === index ? getUpdatedItemAfterProgress(item, loaded, total) : item);
        })
    }

    const fileUploadComplete = (index: number, event: any) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) => item.index === index ? {...item, status: 'Completed', progress: 100} : item);
        })
    }

    const fileUploadError = (index: number, event: any) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) => item.index === index ? {...item, status: 'Failed'} : item);
        })
    }

    const onRetry = () => {
        console.log("Calling on retry")
        verifyPermission(assetDetail?.DirectoryHandle, true)
            .then((result: boolean) => {
                if (result && assetDetail && assetDetail.key && assetDetail.assetId && assetDetail.databaseId && fileUploadTableItems) {
                    setReuploadClicked(true)
                    const uploads = createAssetUploadPromises(fileUploadTableItems, assetDetail.key, {
                            assetId: assetDetail.assetId,
                            databaseId: assetDetail.databaseId,
                        },
                        (index: number, progress: any) => {
                            console.log("Updating progress ")
                            console.log(progress)
                            updateProgressForFileUploadItem(index, progress.loaded, progress.total)
                        },
                        (index: number, event: any) => {
                            console.log("Completed Upload")
                            fileUploadComplete(index, event)
                        }, (index: number, event: any) => {
                            console.log("Error Uploading", event);
                            fileUploadError(index, event)
                        },
                        true,
                    )
                    const uploadComplete = executeUploads(uploads)
                        .then(() => {

                        })
                        .catch((err: any) => {
                            return Promise.reject(err)
                        })
                }
            })
    }
    return <>
        { assetDetail?.Asset &&
            <>
                <Box variant="awsui-key-label">Upload Progress for project {assetDetail.assetName}</Box>
                <FileUploadTable allItems={assetDetail?.Asset} onRetry={onRetry} resume={!reuploadClicked}/>
            </>
        }
    </>
}

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