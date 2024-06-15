import Container from "@cloudscape-design/components/container";
import {
    BreadcrumbGroup,
    Grid,
    Header,
    SegmentedControl,
    SpaceBetween,
} from "@cloudscape-design/components";
import Button from "@cloudscape-design/components/button";
import {
    createContext,
    Dispatch,
    ReducerAction,
    useContext,
    useEffect,
    useReducer,
    useState,
} from "react";
import TextFilter from "@cloudscape-design/components/text-filter";
import "./FileManager.css";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Icon from "@cloudscape-design/components/icon";
import { fetchAssetFiles } from "../../services/APIService";
import { useNavigate, useParams } from "react-router";
//import { Storage } from "aws-amplify";
import { AssetDetail } from "../../pages/AssetUpload";
//import localforage from "localforage";
import { AssetDetailContext, AssetDetailContextType } from "../../context/AssetDetailContext";
import { downloadAsset } from "../../services/APIService";

export interface FileTree {
    name: string;
    relativePath: string;
    keyPrefix: string;
    level: number;
    expanded: boolean;
    subTree: FileTree[];
}

export interface FileManagerStateValues {
    fileTree: FileTree;
    galleryRoot: string;
    actionsRoot: string;
    assetId: string;
    databaseId: string;

    download?: {
        shouldNavigate: boolean;
        fileTree: FileTree;
    };
    upload?: {
        shouldNavigate: boolean;
        fileTree: FileTree;
    };
    view?: {
        shouldNavigate: boolean;
        fileTree: FileTree;
    };
}

type FileManagerState = FileManagerStateValues;

type AssetFileManagerContextType = {
    state: FileManagerState;
    dispatch: any;
};

const AssetFileManagerContext = createContext<AssetFileManagerContextType | undefined>(undefined);

export interface FileManagerAction {
    type: string;
    payload: any;
}

export interface FileKey {
    key: string;
    relativePath: string;
}

function getRootByPath(root: FileTree | null, path: string): FileTree | null {
    if (!root) {
        //console.log("root is null")
        return null;
    }
    if (root.relativePath === path) {
        //console.log("root is path")
        return root;
    } else {
        for (let subtree of root.subTree) {
            if (subtree.relativePath === path) {
                return subtree;
            } else {
                if (subtree.subTree.length > 0) {
                    const subTreeReturn = getRootByPath(subtree, path);
                    if (subTreeReturn) {
                        return subTreeReturn;
                    }
                }
            }
        }
    }
    return null;
}

function addDirectories(root: FileTree, directories: string): FileTree {
    const parts = directories.split("/");
    let currentRoot = root;
    for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        let subTree = currentRoot.subTree.find((subTree) => subTree.name === part);
        if (subTree == null) {
            subTree = {
                name: part,
                relativePath: parts.slice(0, i + 1).join("/") + "/",
                keyPrefix: part,
                level: currentRoot.level + 1,
                expanded: false,
                subTree: [],
            };
            currentRoot.subTree.push(subTree);
        }
        currentRoot = subTree;
    }
    return currentRoot;
}

function addFiles(fileKeys: FileKey[], root: FileTree) {
    //console.log(fileKeys);
    const getParentDirectory = (path: string) => {
        const parentPath = path.split("/").slice(0, -1).join("/");
        return parentPath === "" ? "" : parentPath;
    };
    if (fileKeys.length === 1) {
        root.subTree.push({
            name: fileKeys[0].key.split("/").pop()!,
            relativePath: fileKeys[0].relativePath,
            keyPrefix: fileKeys[0].key,
            level: 1,
            expanded: false,
            subTree: [],
        });
        return root;
    }
    for (let fileKey of fileKeys) {
        //console.log("fileKey", fileKey)
        const parentDir = getParentDirectory(fileKey.relativePath);
        //console.log("parentDir", parentDir)
        let parentRoot = getRootByPath(root, parentDir + "/");
        //console.log("parentRoot", parentRoot)
        if (parentRoot == null) {
            //console.log("parentRoot == null")
            parentRoot = addDirectories(root, parentDir);
        }
        parentRoot!.subTree.push({
            name: fileKey.key.split("/").pop()!,
            relativePath: fileKey.relativePath,
            keyPrefix: fileKey.key,
            level: parentRoot!.level + 1,
            expanded: false,
            subTree: [],
        });
    }
    //console.log(root)
    return root;
}

function toggleExpanded(fileTree: FileTree, relativePath: string): FileTree {
    if (fileTree.relativePath === relativePath) {
        return {
            ...fileTree,
            expanded: !fileTree.expanded,
            subTree: fileTree.subTree.map((subTree) => {
                return {
                    ...subTree,
                    expanded: !subTree.expanded,
                };
            }),
        };
    }
    return {
        ...fileTree,
        subTree: fileTree.subTree.map((subTree) => toggleExpanded(subTree, relativePath)),
    };
}

async function downloadFile(assetId: string, databaseId: string, keyPrefix: string) {
    // //console.log("Downloading file ", keyPrefix)
    // Storage.get(keyPrefix, {
    //     download: false,
    // })
    //     .then((url) => {
    //         //console.log("URL", url)
    //         const link = document.createElement("a");
    //         link.href = url;
    //         link.click();
    //     })
    //     .catch((error) => {
    //         console.log(error);
    //     });

    try {
        const response = await downloadAsset({
            assetId: assetId,
            databaseId: databaseId,
            key: keyPrefix,
            version: "",
        });

        if (response !== false && Array.isArray(response)) {
            if (response[0] === false) {
                // TODO: error handling (response[1] has error message)
                console.error("API Error with downloading file");
            } else {
                const link = document.createElement("a");
                link.href = response[1];
                link.click();
            }
        }
    } catch (error) {
        console.error(error);
    }
}

// type DownloadFileData = {
//     key: string;
//     name: string;
//     relativePath: string;
// };

function fileManagerReducer(state: FileManagerState, action: FileManagerAction): FileManagerState {
    switch (action.type) {
        case "TOGGLE_EXPANDED":
            //console.log("TOGGLE_EXPANDED", action.payload)
            if (!state) {
                return state;
            }
            return {
                ...state,
                fileTree: toggleExpanded(state.fileTree, action.payload.relativePath),
            };

        case "CHANGE_ROOT":
            //console.log("change root", action.payload)
            if (!state) {
                return state;
            }
            return {
                ...state,
                galleryRoot: action.payload.relativePath.endsWith("/")
                    ? action.payload.relativePath
                    : state.galleryRoot,
                actionsRoot: action.payload.relativePath,
            };

        case "DOWNLOAD_FILE":
            //console.log("DOWNLOAD_FILE", action.payload)
            const handleDownloadFile = async () => {
                await downloadFile(state.assetId, state.databaseId, action.payload.key);
            };
            handleDownloadFile();
            return state;

        case "DOWNLOAD_FOLDER":
            //console.log("DOWNLOAD_FOLDER", action.payload)
            if (!state) {
                return state;
            }
            //Re-route to the download folder page
            return {
                ...state,
                download: {
                    shouldNavigate: true,
                    fileTree: action.payload.key,
                },
            };
        case "UPLOAD_FILES":
            //console.log("UPLOAD_FILES", action.payload)
            if (!state) {
                return state;
            }
            //Re-route to the upload folder page
            return {
                ...state,
                upload: {
                    shouldNavigate: true,
                    fileTree: action.payload.key,
                },
            };
        case "VIEW_FILE_FOLDER":
            //console.log("VIEW_FILE_FOLDER", action.payload)
            if (!state) {
                return state;
            }
            return {
                ...state,
                view: {
                    shouldNavigate: true,
                    fileTree: action.payload.key,
                },
            };

        case "RESET_DOWNLOAD":
            //console.log("RESET_DOWNLOAD", action.payload)
            if (!state) {
                return state;
            } else {
                return {
                    ...state,
                    download: undefined,
                };
            }
        case "RESET_UPLOAD":
            //console.log("RESET_UPLOAD", action.payload)
            if (!state) {
                return state;
            } else {
                return {
                    ...state,
                    upload: undefined,
                };
            }
        case "FETCH_SUCCESS":
            //console.log("FETCH_SUCCESS", action.payload)
            return {
                ...state,
                fileTree: action.payload,
                galleryRoot: "/",
                actionsRoot: "/",
            };
        case "FETCH_ERROR":
            console.log("FETCH_ERROR", action.payload);
            return state;
        default:
            return state;
    }
}

// function FileManagerControl() {
//     const [filteringText, setFilteringText] = useState("");
//     return (
//         <div>
//             <Grid gridDefinition={[{ colspan: 7 }, { colspan: 5 }]}>
//                 <div>
//                     <TextFilter
//                         filteringText={filteringText}
//                         filteringPlaceholder="Search Files"
//                         filteringAriaLabel="Search Files"
//                         onChange={({ detail }) => setFilteringText(detail.filteringText)}
//                     />
//                 </div>
//                 <div style={{ float: "right" }}>
//                     <SpaceBetween direction="horizontal" size="xs">
//                         <Button>Upload File</Button>
//                         <Button variant={"primary"}>Upload Folder</Button>
//                     </SpaceBetween>
//                 </div>
//             </Grid>
//         </div>
//     );
// }

function FileTreeBlock(props: {
    level: number;
    isFolder: boolean;
    name: string;
    relativePath: string;
    expanded: boolean;
    dispatch: Dispatch<ReducerAction<typeof fileManagerReducer>>;
}) {
    return (
        <ColumnLayout>
            <div style={{ minHeight: "25px", paddingLeft: (props.level + 1) * 10 + "px" }}>
                {props.isFolder && (
                    <span
                        onClick={() => {
                            props.dispatch({
                                type: "TOGGLE_EXPANDED",
                                payload: { relativePath: props.relativePath },
                            });
                        }}
                    >
                        {props.expanded ? (
                            <>
                                <Icon name="caret-down-filled" />
                                <Icon name="folder-open" />
                            </>
                        ) : (
                            <>
                                <Icon name="caret-right-filled" />
                                <Icon name="folder" />
                            </>
                        )}
                    </span>
                )}
                {!props.isFolder && (
                    <span style={{ paddingLeft: "20px" }}>
                        <Icon name="file" />
                    </span>
                )}
                <span
                    style={{ paddingLeft: "2px", fontSize: "16px", cursor: "default" }}
                    onClick={() => {
                        props.dispatch({
                            type: "CHANGE_ROOT",
                            payload: { relativePath: props.relativePath },
                        });
                    }}
                >
                    {props.name}
                </span>
            </div>
        </ColumnLayout>
    );
}

function FileTreeView({ root }: { root: FileTree }) {
    const { state, dispatch } = useContext(AssetFileManagerContext) as AssetFileManagerContextType;
    if (!state) {
        return <>Loading...</>;
    }
    return (
        <>
            <FileTreeBlock
                level={root.level}
                isFolder={root.subTree.length > 0}
                name={root.name}
                relativePath={root.relativePath}
                expanded={root.expanded}
                dispatch={dispatch}
            />
            {root.subTree.map(
                (subTree) =>
                    root.expanded && <FileTreeView key={subTree.keyPrefix} root={subTree} />
            )}
        </>
    );
}

function FolderActionBar(props: { actionsBarRoot: FileTree }) {
    const { state, dispatch } = useContext(AssetFileManagerContext) as AssetFileManagerContextType;
    return (
        <div className="action-bar">
            <SpaceBetween size={"l"} direction={"horizontal"}>
                <div className="action-bar-item">
                    <Icon name={"download"} />
                    <span
                        onClick={() => {
                            dispatch({
                                type: "DOWNLOAD_FOLDER",
                                payload: {
                                    key: props.actionsBarRoot,
                                },
                            });
                        }}
                    >
                        {" "}
                        Download {props.actionsBarRoot.name}{" "}
                    </span>
                </div>
                <div className="action-bar-item">
                    <Icon name={"upload"} />
                    <span
                        onClick={() => {
                            dispatch({
                                type: "UPLOAD_FILES",
                                payload: { key: props.actionsBarRoot },
                            });
                        }}
                    >
                        {" "}
                        Upload Files in {props.actionsBarRoot.name}{" "}
                    </span>
                </div>
                <div className="action-bar-item">
                    <Icon name={"external"} />
                    <span
                        onClick={() => {
                            dispatch({
                                type: "VIEW_FILE_FOLDER",
                                payload: { key: props.actionsBarRoot },
                            });
                        }}
                    >
                        {" "}
                        View {props.actionsBarRoot.name} Metadata{" "}
                    </span>
                </div>
            </SpaceBetween>
        </div>
    );
}

function FileActionBar(props: { actionsBarRoot: FileTree }) {
    const { state, dispatch } = useContext(AssetFileManagerContext) as AssetFileManagerContextType;

    return (
        <>
            <SpaceBetween size={"xs"} direction={"horizontal"}>
                <div className="action-bar-item">
                    <Icon name={"download"} />
                    <span
                        onClick={() => {
                            dispatch({
                                type: "DOWNLOAD_FILE",
                                payload: {
                                    key: props.actionsBarRoot.keyPrefix,
                                },
                            });
                        }}
                    >
                        {" "}
                        Download {props.actionsBarRoot.name}{" "}
                    </span>
                </div>
                {/* <div className="action-bar-item">
                <Icon name={"delete-marker"}/>
                <span> Delete {props.actionsBarRoot.name} </span>
            </div> */}
                <div className="action-bar-item">
                    <Icon name={"external"} />
                    <span
                        onClick={() => {
                            dispatch({
                                type: "VIEW_FILE_FOLDER",
                                payload: { key: props.actionsBarRoot },
                            });
                        }}
                    >
                        {" "}
                        View {props.actionsBarRoot.name}
                    </span>
                </div>
            </SpaceBetween>
        </>
    );
}

function FileBrowserTopControl() {
    const { state, dispatch } = useContext(AssetFileManagerContext) as AssetFileManagerContextType;
    if (!state) {
        return <>Loading...</>;
    }

    let actionsBarRoot = getRootByPath(state!.fileTree, state!.actionsRoot);
    if (actionsBarRoot == null) {
        actionsBarRoot = state!.fileTree;
    }

    return (
        <div className="gallery-top-control">
            <div className="gallery-top-control-wrapper">
                {actionsBarRoot.subTree.length > 0 ? (
                    <FolderActionBar actionsBarRoot={actionsBarRoot} />
                ) : (
                    <FileActionBar actionsBarRoot={actionsBarRoot} />
                )}
            </div>
        </div>
    );
}

function FileBrowserGalleryItem({ root }: { root: FileTree }) {
    const { state, dispatch } = useContext(AssetFileManagerContext) as AssetFileManagerContextType;
    if (!state) {
        return <>Loading...</>;
    }

    return (
        <div
            className="galleryItem"
            onDoubleClick={() =>
                dispatch({ type: "CHANGE_ROOT", payload: { relativePath: root.relativePath } })
            }
        >
            <Icon name={root.subTree.length > 0 ? "folder" : "file"} size={"large"} />
            <div className="gallaryItemName">{root.name}</div>
        </div>
    );
}

function FileBrowserGalleryView({ root }: { root: FileTree }) {
    return (
        <div className="gallery">
            {root.subTree.map((subTree) => (
                <FileBrowserGalleryItem key={subTree.keyPrefix} root={subTree} />
            ))}
        </div>
    );
}

function FileBrowser() {
    const { state, dispatch } = useContext(AssetFileManagerContext) as AssetFileManagerContextType;
    if (!state) {
        return <>Loading...</>;
    }

    let galleryViewRoot = getRootByPath(state.fileTree, state.galleryRoot);
    if (galleryViewRoot == null) {
        galleryViewRoot = state.fileTree;
    }
    return (
        <div className="gallery-wrapper">
            <FileBrowserTopControl />
            <FileBrowserGalleryView root={galleryViewRoot} />
        </div>
    );
}

function FileTreeWrapper() {
    const { state, dispatch } = useContext(AssetFileManagerContext) as AssetFileManagerContextType;
    if (!state) {
        return <>Loading...</>;
    }
    return (
        <div className="wrapper">
            <div className="wrapper-left">
                <h4> Files </h4>
                <FileTreeView root={state.fileTree} />
            </div>
            <div className="wrapper-right">
                <FileBrowser />
            </div>
        </div>
    );
}

export function FileManager({ assetName }: { assetName: string }) {
    const { databaseId, assetId } = useParams();
    const { state: assetDetailState } = useContext(AssetDetailContext) as AssetDetailContextType;
    const navigate = useNavigate();
    const initialState = {
        fileTree: {
            name: assetName,
            relativePath: "/",
            keyPrefix: "/",
            level: 0,
            expanded: true,
            subTree: [],
        },
        galleryRoot: "/",
        actionsRoot: "/",
        assetId: assetId!,
        databaseId: databaseId!,
    };
    const [state, dispatch] = useReducer(fileManagerReducer, initialState);

    useEffect(() => {
        const fetchData = async () => {
            try {
                const response = await fetchAssetFiles({ databaseId, assetId });
                //console.log("fileManagerResponse", response)
                const fileTree = addFiles(response, initialState.fileTree);
                dispatch({ type: "FETCH_SUCCESS", payload: fileTree });
            } catch (error) {
                dispatch({ type: "FETCH_ERROR", payload: "" });
            }
        };
        fetchData();
    }, [databaseId, assetId]);

    useEffect(() => {
        if (state.download && state.download.shouldNavigate) {
            navigate(`/databases/${databaseId}/assets/${assetId}/download`, {
                state: { fileTree: state.download.fileTree },
            });
            dispatch({ type: "RESET_DOWNLOAD", payload: null });
        }
        if (state.upload && state.upload.shouldNavigate) {
            navigate(`/databases/${databaseId}/assets/${assetId}/uploads`, {
                state: {
                    fileTree: state.upload.fileTree,
                    assetDetailState: assetDetailState,
                    isNewFiles: true,
                },
            });
            dispatch({ type: "RESET_UPLOAD", payload: null });
        }
        if (state.view && state.view.shouldNavigate) {
            //console.log("VIEW")
            navigate(`/databases/${databaseId}/assets/${assetId}/file`, {
                state: {
                    filename: state.view.fileTree.name,
                    key: state.view.fileTree.keyPrefix,
                    isDirectory: state.view.fileTree.subTree.length > 0,
                },
            });
            dispatch({ type: "RESET_VIEW", payload: null });
        }
        return () => {};
    }, [state.download, state.upload, state.view]);

    return (
        <Container header={<Header variant="h2">File Manager</Header>}>
            {state && (
                <AssetFileManagerContext.Provider value={{ state, dispatch }}>
                    {/* <FileManagerControl/> */}
                    <FileTreeWrapper />
                </AssetFileManagerContext.Provider>
            )}
        </Container>
    );
}
