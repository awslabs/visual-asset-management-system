import Container from "@cloudscape-design/components/container";
import {BreadcrumbGroup, Grid, Header, SegmentedControl, SpaceBetween} from "@cloudscape-design/components";
import Button from "@cloudscape-design/components/button";
import {useState} from "react";
import TextFilter from "@cloudscape-design/components/text-filter";
import "./FileManager.css"
import ColumnLayout from "@cloudscape-design/components/column-layout";

export interface FileManagerProps {
    filePaths: [string]
    key: string
}

function FileManagerControl() {
    const [
        filteringText,
        setFilteringText
    ] = useState("");
    return (
        <div>
            <Grid
                gridDefinition={[{ colspan: 7 }, { colspan: 5 }]}
            >
                <div>
                    <TextFilter
                        filteringText={filteringText}
                        filteringPlaceholder="Search Files"
                        filteringAriaLabel="Search Files"
                        onChange={({ detail }) =>
                            setFilteringText(detail.filteringText)
                        }
                    />
                </div>
                <div style={{float: "right" }}>
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button>
                            Upload File
                        </Button>
                        <Button variant={"primary"}>
                            Upload Folder
                        </Button>
                    </SpaceBetween>
                </div>
            </Grid>
        </div>
    )
}

function FileTree() {
    return (
        <div>
            File Tree Browser
        </div>
    );
}

function FileBrowserTopControl() {
    return (
        <div className="gallery-top-control">
            <div className="gallery-top-control-wrapper">
                <Grid
                    gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}
                >
                    <BreadcrumbGroup items={[{text: "test1", href:""}, {text: "test2", href:""}]} />
                    <div style={{float: "right"}}>
                        <SegmentedControl
                            selectedId={"gallary"}
                            options={[
                                {id: "gallary", text: "Gallary"},
                                {id: "list", text: "List"}
                            ]}
                        />
                    </div>
                </Grid>
            </div>
        </div>
    );
}

function FileBrowser() {
    return (
        <div>
            <FileBrowserTopControl></FileBrowserTopControl>
        </div>
    )
}

function FileTreeWrapper() {
    return (
        <div className="wrapper">
            <div className="wrapper-left">
                <FileTree />
            </div>
            <div className="wrapper-right">
                <FileBrowser />
            </div>
        </div>
    )
}

export function FileManager() {

    return (
        <Container
            header={
                <Header
                    variant="h2"
                >
                File Manager
                </Header>
            }
        >
            <FileManagerControl />
            <FileTreeWrapper />
        </Container>
    )
}