import { useEffect, useState } from "react";
import {
    Container,
    FormField,
    Header,
    Input,
    Select,
    SpaceBetween,
    Textarea,
} from "@cloudscape-design/components";
import { z } from "zod";
import DatabaseSelector from "../../components/selectors/DatabaseSelector";
import Synonyms from "../../synonyms";
import { isDistributableOptions } from "./constants";
import { useAssetUploadState } from "./state";

const pageOneSchema = z
    .object({
        assetId: z
            .string()
            .min(3)
            .regex(/^[a-z][a-z0-9-_]{3,63}$/),
        isDistributable: z.boolean(),
        databaseId: z.string().min(4),
        description: z.string().min(4),
        Comment: z.string().min(4),
    })
    .passthrough();

export const AssetPrimaryInfo = () => {
    const [state, dispatch] = useAssetUploadState();
    const [dirtyFields, setDirtyFields] = useState<Record<string, boolean | null>>({});
    const [validity, setValidity] = useState<Record<string, string | null>>({});

    useEffect(() => {
        const pageOne = {
            assetId: state.assetId,
            isDistributable: state.isDistributable,
            databaseId: state.databaseId,
            description: state.description,
            Comment: state.Comment,
        };

        const result = pageOneSchema.safeParse(pageOne);
        if (!result.success) {
            dispatch({ type: "UPDATE_PAGE_VALIDITY", payload: false });
            const { issues } = result.error;
            const issueMap: Record<string, string | null> = Object.keys(pageOne).reduce(
                (acc, cur) => {
                    const message =
                        issues.find((issue) => issue.path.includes(cur))?.message || null;
                    acc[cur] = message;
                    return acc;
                },
                {} as Record<string, string | null>
            );
            setValidity(issueMap);
        } else {
            setValidity({});
            dispatch({ type: "UPDATE_PAGE_VALIDITY", payload: true });
        }
    }, [
        dispatch,
        state.assetId,
        state.isDistributable,
        state.databaseId,
        state.description,
        state.Comment,
    ]);

    const setDirty = (controlId: string) => {
        setDirtyFields({
            ...dirtyFields,
            [controlId]: true,
        });
    };

    const isDirty = (controlId: string) => dirtyFields[controlId];

    const setErrorText = (controlId: string) =>
        isDirty(controlId) && validity[controlId] ? validity[controlId] : "";

    return (
        <Container header={<Header variant="h2">{Synonyms.Asset} Details</Header>}>
            <SpaceBetween direction="vertical" size="l">
                <FormField label={`${Synonyms.Asset} Name`} errorText={setErrorText("assetId")}>
                    <Input
                        controlId="assetId"
                        value={state.assetId}
                        data-testid="assetid-input"
                        onChange={(e) => {
                            setDirty("assetId");
                            dispatch({
                                type: "UPDATE_ASSET_ID",
                                payload: e.detail.value,
                            });
                        }}
                        ariaRequired
                    />
                </FormField>

                <FormField label="Is Distributable?" errorText={setErrorText("isDistributable")}>
                    <Select
                        controlId="isDistributable"
                        options={isDistributableOptions}
                        selectedOption={
                            isDistributableOptions
                                .filter(
                                    (o) =>
                                        (state.isDistributable === false ? "No" : "Yes") === o.label
                                )
                                .pop() || isDistributableOptions[0]
                        }
                        onChange={({ detail }) => {
                            setDirty("isDistributable");
                            dispatch({
                                type: "UPDATE_ASSET_DISTRIBUTABLE",
                                payload: detail.selectedOption.label === "Yes",
                            });
                        }}
                        filteringType="auto"
                        selectedAriaLabel="Selected"
                        data-testid="isDistributable-select"
                        ariaRequired
                    />
                </FormField>

                <FormField label={Synonyms.Database} errorText={setErrorText("databaseId")}>
                    <DatabaseSelector
                        controlId="databaseId"
                        onChange={(x: any) => {
                            setDirty("databaseId");
                            dispatch({
                                type: "UPDATE_ASSET_DATABASE",
                                payload: x.detail.selectedOption.value,
                            });
                        }}
                        selectedOption={{
                            label: state.databaseId,
                            value: state.databaseId,
                        }}
                        data-testid="database-selector"
                        ariaRequired
                    />
                </FormField>

                <FormField
                    label="Description"
                    constraintText="Minimum 4 characters"
                    errorText={setErrorText("description")}
                >
                    <Textarea
                        controlId="description"
                        value={state.description || ""}
                        onChange={(e) => {
                            setDirty("description");
                            dispatch({
                                type: "UPDATE_ASSET_DESCRIPTION",
                                payload: e.detail.value,
                            });
                        }}
                        data-testid="asset-description-textarea"
                        ariaRequired
                    />
                </FormField>

                <FormField
                    label="Comment"
                    constraintText="Minimum 4 characters"
                    errorText={setErrorText("Comment")}
                >
                    <Input
                        controlId="Comment"
                        value={state.Comment || ""}
                        onChange={(e) => {
                            setDirty("Comment");
                            dispatch({
                                type: "UPDATE_ASSET_COMMENT",
                                payload: e.detail.value,
                            });
                        }}
                        data-testid="asset-comment-input"
                        ariaRequired
                    />
                </FormField>
            </SpaceBetween>
        </Container>
    );
};
