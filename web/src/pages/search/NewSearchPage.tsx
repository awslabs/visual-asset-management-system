/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from 'react';
import { useParams } from 'react-router';
import { Box, BreadcrumbGroup } from '@cloudscape-design/components';
import { ModernSearchContainer } from '../../components/search';
import Synonyms from '../../synonyms';

interface NewSearchPageProps {}

const NewSearchPage: React.FC<NewSearchPageProps> = () => {
    const { databaseId } = useParams();

    return (
        <Box padding={{ top: databaseId ? 's' : 'm', horizontal: 'l' }}>
            {databaseId && (
                <BreadcrumbGroup
                    items={[
                        { text: Synonyms.Databases, href: '#/databases/' },
                        {
                            text: databaseId,
                            href: `#/databases/${databaseId}/assets/`,
                        },
                        { text: Synonyms.Assets, href: '' },
                    ]}
                    ariaLabel="Breadcrumbs"
                />
            )}
            
            <ModernSearchContainer
                mode="full"
                databaseId={databaseId}
                allowedViews={['table', 'card', 'map']}
                showPreferences={true}
                showBulkActions={true}
            />
        </Box>
    );
};

export default NewSearchPage;
