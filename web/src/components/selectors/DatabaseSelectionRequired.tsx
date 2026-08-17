/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import DatabaseSelector from "./DatabaseSelector";
import Synonyms from "../../synonyms";

interface DatabaseSelectionRequiredProps {
    /** Page title, so the user still knows where they are while choosing. */
    title: string;
    /** What the choice governs, e.g. "Tags are managed per database." */
    description?: string;
    /** Receives the DatabaseSelector change event; read `detail.selectedOption.value`. */
    onSelect: (event: any) => void;
    /** Offer GLOBAL alongside the real databases (default true). */
    showGlobal?: boolean;
}

/**
 * The mandatory database choice a page makes before it can display anything.
 *
 * Rendered inline rather than in a modal on purpose. Cloudscape's Modal always renders a dismiss
 * control, so a page that showed *only* a modal on first load could be closed to reveal nothing behind
 * it — the page looked broken. Inline, the choice cannot be escaped and there is always something on
 * screen.
 *
 * Once a database IS selected, offer "Change {Database}" through `DatabaseSelectorWithModal`: there a
 * dismiss is safe, because it returns to a populated page.
 */
export default function DatabaseSelectionRequired({
    title,
    description,
    onSelect,
    showGlobal = true,
}: DatabaseSelectionRequiredProps) {
    return (
        <Box padding={{ top: "m", horizontal: "l" }}>
            <SpaceBetween size="l">
                <Header variant="h1" description={description}>
                    {title}
                </Header>
                <Container header={<Header variant="h2">{`Select ${Synonyms.Database}`}</Header>}>
                    <SpaceBetween size="m">
                        <Box variant="p">
                            {`Choose a ${Synonyms.database} to continue.`}
                            {showGlobal
                                ? ` Choose GLOBAL for entries shared by every ${Synonyms.database}.`
                                : ""}
                        </Box>
                        <DatabaseSelector onChange={onSelect} showGlobal={showGlobal} />
                    </SpaceBetween>
                </Container>
            </SpaceBetween>
        </Box>
    );
}
