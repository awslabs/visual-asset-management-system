import { Header } from "@cloudscape-design/components";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import { Storage } from "@aws-amplify/storage";
import { useEffect, useState } from "react";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import { useNavigate, useParams } from "react-router"
export class FolderActionProps {
    databaseId!: string
    assetId!: string;
    name!: string;
    urlKey!: string;
    isDirectory!: boolean;
}

export default function FolderActionViewer({ name, urlKey, ...props }: FolderActionProps) {
    const navigate = useNavigate()
    const [downloadLink, setDownloadLink] = useState<string>("");

    function generateDownloadLink(key: string) {
        Storage.get(key, { download: false }).then((data) => {
            setDownloadLink(data);
        });
    }

    function navigateToAssetFilePage() {
        navigate(`/databases/${props.databaseId}/assets/${props.assetId}/file`, { state: { 'filename': name, 'key': urlKey, 'isDirectory': props.isDirectory }})
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
                        <p>Selected {props.isDirectory ? 'directory' : 'file'} : {name}</p>

                        {!props.isDirectory && (
                            <>
                                <ColumnLayout columns={2}>
                                    <Button variant="primary" onClick={() => generateDownloadLink(urlKey)}>
                                        Generate download link
                                    </Button>
                                </ColumnLayout>
                                <p>
                                    {downloadLink && (
                                        <Button href={downloadLink} target="_blank" rel="noreferrer">
                                            Download {name}
                                        </Button>
                                    )}
                                </p>
                            </>
                        )}
                        <p>

                            <Button variant={"primary"} onClick={() => navigateToAssetFilePage()}>
                                View { props.isDirectory ? 'directory' : 'file'}
                            </Button>

                        </p>
                    </>
                )}
            </div>
        </Container>
    );
}
