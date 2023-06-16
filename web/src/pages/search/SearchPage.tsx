import React, {useState} from 'react';
import SearchPropertyFiter from "./SearchPropertyFiter";
import Container from "@cloudscape-design/components/container";
import {ColumnLayout, Grid, Header} from "@cloudscape-design/components";
import Synonyms from "../../synonyms";
import SearchPageSegmentedControl from "./SearchPageSegmentedControl";
import SearchPageMapView from "./SearchPageMapView";
import SearchPageListView from "./SearchPageListView";
import Box from "@cloudscape-design/components/box";

interface SearchPageProps {

}

function SearchPage(props: SearchPageProps) {
    const [viewSelected, setViewSelected] = useState<String>('mapview')
    return (
        <Container
            header={
            <Header
                variant="h2"
            >
                Search {Synonyms.Assets}
            </Header>
        }>
            <ColumnLayout columns={2}>
                <SearchPropertyFiter />
                <div style={{display: 'flex', justifyContent:'flex-end'}}>
                    <SearchPageSegmentedControl onchange={(selectedId: string) => setViewSelected(selectedId)}/>
                </div>
            </ColumnLayout>
            <Grid>
            {
                viewSelected === 'mapview' ?
                        <SearchPageMapView/>
                     :
                    <Box>
                        <SearchPageListView />
                    </Box>
            }
            </Grid>
        </Container>

    );
}

export default SearchPage;