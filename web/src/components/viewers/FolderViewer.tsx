import React, {useEffect, useState} from "react";
import FolderTree, {NodeData} from "react-folder-tree";
import "react-folder-tree/dist/style.css";
import {fetchAssetFiles} from "../../services/APIService";
import Container from "@cloudscape-design/components/container";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import {Header} from "@cloudscape-design/components";
import FolderActionViewer, {FolderActionProps} from "./FolderActionViewer";

class FolderViewerProps {
    databaseId!: string;
    assetId!: string;
    assetName!: string;
}

class AssetFileList {
    key!: string;
    relativePath!: string;
}

export default function FolderViewer({ databaseId, assetId, assetName }: FolderViewerProps) {
    const [folderActionProps, setFolderActionProps] = useState<FolderActionProps>({
        databaseId,
        assetId,
        name: "",
        urlKey: "",
        isDirectory: true,
    });

    const [treeState, setTreeState] = useState<NodeData>({
        name: "",
        isOpen: true,
        children: [],
    });
    const [reload, setReload] = useState(true);
    const convertFileListToDataSet = (fileList: AssetFileList[]) => {
        const root: NodeData = { name: assetName, isOpen: true};
        const rootChildren: NodeData[] = [];

        for (const filePath of fileList) {
            const components = filePath.relativePath.split('/');

            const fileName = components.pop()!; // Extract the file name
            console.log("components are ")
            console.log(components)
            if(!components || components.length === 0) {
                console.log("components length is 0")
                rootChildren.push({name: filePath.relativePath || filePath.key, isOpen: true, key:filePath.key})
            } else {
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
        }
        root.children = rootChildren;
        console.log(root)
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
        let parents = [assetId]
        for (let i = 0; i < nodeData.path.length; i++) {
            console.log(nodeData.path[i]);
            const p = nodeData.path[i];
            if (root.children) {
                // @ts-ignore
                const child = root.children[p];
                parents.push(child.name)
                root = child;
            }
        }
        if (!root.children) {
            setFolderActionProps((prevState) => {
                return {
                    ...prevState,
                    name: root.name,
                    urlKey: root.key,
                    isDirectory: false,
                };
            });
        } else {
            setFolderActionProps((prevState) => {
                return {
                    ...prevState,
                    isDirectory: true,
                    name: root.name,
                    urlKey: parents.join("/") + "/"
                }
            })
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
                        databaseId={folderActionProps.databaseId}
                        assetId={folderActionProps.assetId}
                        name={folderActionProps.name}
                        urlKey={folderActionProps.urlKey}
                        isDirectory={folderActionProps.isDirectory}
                    />
                </div>
            </ColumnLayout>
        </div>
    );
}
