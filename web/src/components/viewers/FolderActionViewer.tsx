import {Header, SpaceBetween} from "@cloudscape-design/components";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import { Storage } from "@aws-amplify/storage";
import {useEffect, useState} from "react";
import ColumnLayout from "@cloudscape-design/components/column-layout";

export class FolderActionProps {
    name!: string;
    urlKey!: string;

    [key: string]: any;
}

export default function FolderActionViewer({name, urlKey, ...props}: FolderActionProps) {
    const [downloadLink, setDownloadLink] = useState<string>('');

    function generateDownloadLink(key: string) {
        Storage.get(key, {download: false}).then(data => {
            setDownloadLink(data)
        })
    }

    useEffect(() => {
        setDownloadLink('')
    }, [name])

    return (
        <Container>
            <div>
                <Header variant="h3">
                    Actions
                </Header>
                {
                    name &&
                    <>
                        <p>Selected file: {name}</p>

                        <ColumnLayout columns={2}>
                            <Button variant="primary" onClick={() => generateDownloadLink(urlKey)}>
                                Generate download link
                            </Button>
                            <Button>
                                Upload a new version
                            </Button>
                        </ColumnLayout>
                        <p>
                            {
                                downloadLink &&
                                <Button href={downloadLink} target="_blank" rel="noreferrer">
                                    Download {name}
                                </Button>
                            }
                        </p>
                    </>
                }
            </div>
        </Container>

    );
}