import React, { useEffect, useState } from "react";
import { Storage } from "aws-amplify";
import FolderTree, { FolderTreeProps, NodeData, testData } from "react-folder-tree";
import "react-folder-tree/dist/style.css";
import { fetchAssetFiles } from "../../services/APIService";
import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import { Header } from "@cloudscape-design/components";
import FolderActionViewer, { FolderActionProps } from "./FolderActionViewer";
class FolderViewerProps {
    databaseId!: string;
    assetId!: string;
}

class AssetFileList {
    key!: string;
    relativePath!: string;
}

export default function FolderViewer({ databaseId, assetId }: FolderViewerProps) {
    const [folderActionProps, setFolderActionProps] = useState<FolderActionProps>({
        name: "",
        urlKey: "",
    });

    const [treeState, setTreeState] = useState<NodeData>({
        name: "",
        isOpen: true,
        children: [],
    });
    const [reload, setReload] = useState(true);
    const convertFileListToDataSet = (fileList: AssetFileList[]) => {
        let tempTreeState: NodeData = {
            name: "",
            isOpen: false,
            children: [],
        };

        if (fileList.length > 0) {
            tempTreeState.name = fileList[0].relativePath.split("/")[0];
        }
        for (let i = 0; i < fileList.length; i++) {
            let root = tempTreeState.children;
            const splits = fileList[i].relativePath.split("/");
            //First split is always the root folder so skip adding that since its already added
            for (let j = 1; j < splits.length; j++) {
                let child = root?.find((x) => x.name === splits[j]);
                if (child) {
                    root = child.children;
                } else {
                    const newChild: NodeData = {
                        name: splits[j],
                        isOpen: false,
                        children: j === splits.length - 1 ? undefined : [],
                        key: j === splits.length - 1 ? fileList[i].key : undefined,
                    };
                    root?.push(newChild);
                    root = newChild.children;
                }
            }
        }
        return tempTreeState;
    };
    useEffect(() => {
        const fetchAssetData = async () => {
            const fileList = await fetchAssetFiles({ databaseId, assetId });
            console.log(fileList);
            return convertFileListToDataSet(fileList);
        };

        if (reload) {
            fetchAssetData().then((newTreeState) => {
                console.log("Updating tree state to ", newTreeState);
                setTreeState(newTreeState);
            });
            setReload(false);
        }
    }, [databaseId, assetId, reload]);

    //@ts-ignore
    const onNameClick = ({ defaultOnClick, nodeData }) => {
        console.log("Clicked on ", nodeData);
        defaultOnClick();
        let root = treeState;
        for (let i = 0; i < nodeData.path.length; i++) {
            console.log(nodeData.path[i]);
            const p = nodeData.path[i];
            if (root.children) {
                // @ts-ignore
                const child = root.children[p];
                root = child;
            }
        }
        if (root.key) {
            setFolderActionProps((prevState) => {
                return {
                    ...prevState,
                    name: root.name,
                    urlKey: root.key,
                };
            });
        }
    };

    return (
        <div style={{ height: "100%", overflow: "auto", background: "#ffffff" }}>
            <ColumnLayout columns={2}>
                <Container>
                    <div style={{ height: "100%", overflow: "auto" }}>
                        <Header variant="h3">Folder</Header>
                        <FolderTree
                            data={treeState}
                            readOnly
                            showCheckbox={false}
                            onNameClick={onNameClick}
                            initOpenStatus="closed"
                        />
                    </div>
                </Container>
                <div>
                    <FolderActionViewer
                        name={folderActionProps.name}
                        urlKey={folderActionProps.urlKey}
                    />
                </div>
            </ColumnLayout>
        </div>
    );
}
