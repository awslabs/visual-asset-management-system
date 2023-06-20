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
        const root: NodeData = { name: "root", isOpen: true};
        const rootChildren: NodeData[] = [];

        for (const filePath of fileList) {
            const components = filePath.relativePath.split('/'); // Adjust the separator based on your file system

            const fileName = components.pop()!; // Extract the file name

            let currentChildren = rootChildren;
            for (const component of components) {
                let foundChild: NodeData | undefined = currentChildren.find(child => child.name === component);

                if (!foundChild) {
                    foundChild = { name: component, isOpen: false};
                    currentChildren.push(foundChild);
                    foundChild.children = [];
                }

                currentChildren = foundChild.children!;
            }

            currentChildren.push({name: fileName, isOpen: true, key:filePath.key}); // Add the file name as a child node with the key
        }
        root.children = rootChildren;
        return root;
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
                            initOpenStatus="open"
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
