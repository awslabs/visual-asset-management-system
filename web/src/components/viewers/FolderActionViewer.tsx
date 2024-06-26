import { Header } from "@cloudscape-design/components";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import { useEffect, useState } from "react";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import { useNavigate, useParams } from "react-router";
import { downloadAsset } from "../../services/APIService";
export class FolderActionProps {
    databaseId!: string;
    assetId!: string;
    isDistributable!: boolean;
    name!: string;
    urlKey!: string;
    isDirectory!: boolean;
}

export default function FolderActionViewer({
    assetId,
    databaseId,
    name,
    urlKey,
    ...props
}: FolderActionProps) {
    const navigate = useNavigate();
    const [downloadLink, setDownloadLink] = useState<string>("");

    async function generateDownloadLink(key: string) {
        try {
            const response = await downloadAsset({
                assetId: assetId,
                databaseId: databaseId,
                key: key,
                version: "",
            });

            if (response !== false && Array.isArray(response)) {
                if (response[0] === false) {
                    // TODO: error handling (response[1] has error message)
                } else {
                    setDownloadLink(response[1]);
                }
            }
        } catch (error) {
            console.error(error);
        }
    }

    function navigateToAssetFilePage() {
        navigate(`/databases/${databaseId}/assets/${assetId}/file`, {
            state: { filename: name, key: urlKey, isDirectory: props.isDirectory },
        });
    }

    useEffect(() => {
        setDownloadLink("");
    }, [name]);

    return (
        <Container>
            <div>
                <Header variant="h3">Actions</Header>
                {name && (
                    <>
                        <p>
                            Selected {props.isDirectory ? "directory" : "file"} : {name}
                        </p>

                        {!props.isDirectory && props.isDistributable && (
                            <>
                                <ColumnLayout columns={4}>
                                    <Button
                                        variant="primary"
                                        onClick={() => generateDownloadLink(urlKey)}
                                    >
                                        Generate download link
                                    </Button>
                                    <p>
                                        {downloadLink && (
                                            <Button
                                                href={downloadLink}
                                                target="_blank"
                                                rel="noreferrer"
                                            >
                                                Download {name}
                                            </Button>
                                        )}
                                    </p>
                                </ColumnLayout>
                            </>
                        )}
                        <p>
                            <Button variant={"primary"} onClick={() => navigateToAssetFilePage()}>
                                View {props.isDirectory ? "directory" : "file"}
                            </Button>
                        </p>
                    </>
                )}
            </div>
        </Container>
    );
}
