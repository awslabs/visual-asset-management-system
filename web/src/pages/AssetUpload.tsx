/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createContext, Dispatch, useContext, useEffect, useReducer, useState } from "react";
import {
    Box,
    Button,
    ColumnLayout,
    Container,
    FormField,
    Grid,
    Header,
    Input,
    Modal,
    ProgressBarProps,
    Select,
    SpaceBetween,
    StatusIndicatorProps,
    Textarea,
    TextContent,
    Toggle,
    Wizard,
    Multiselect,
    Form,
    Link,
    Table,
} from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { Cache } from "aws-amplify";
import { useNavigate } from "react-router";
import DatabaseSelector from "../components/selectors/DatabaseSelector";
import { previewFileFormats } from "../common/constants/fileFormats";
import { Metadata } from "../components/single/Metadata";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";
import {
    validateNonZeroLengthTextAsYouType,
    validateRequiredTagTypeSelected,
} from "./AssetUpload/validations";
import { DisplayKV, FileUpload } from "./AssetUpload/components";
import ProgressScreen from "./AssetUpload/ProgressScreen";
import ControlledMetadata from "../components/metadata/ControlledMetadata";
import Synonyms from "../synonyms";
import onSubmit, { onUploadRetry, UploadExecutionProps } from "./AssetUpload/onSubmit";
import FolderUpload from "../components/form/FolderUpload";
import { FileUploadTable, FileUploadTableItem, shortenBytes } from "./AssetUpload/FileUploadTable";
import localforage from "localforage";
import { fetchTags, fetchAllAssets, fetchtagTypes } from "../services/APIService";
import CustomTable from "../components/table/CustomTable";
import { featuresEnabled } from "../common/constants/featuresEnabled";
import { TagType } from "./Tag/TagType.interface";

const previewFileFormatsStr = previewFileFormats.join(", ");
var tags: any[] = [];
var tagTypes: TagType[] = [];
var assetOptions: { label: string; value: string }[] = [];
var assetTags: string[] = [];

export class AssetDetail {
    isMultiFile: boolean = false;
    assetId?: string;
    assetName?: string;
    databaseId?: string;
    description?: string;
    frontendTags?: { label: string; value: string }[];
    tags?: string[];
    assetLinksFe?: {
        parents?: any[];
        child?: any[];
        related?: any[];
    };
    assetLinks?: {
        parents?: any[];
        child?: any[];
        related?: any[];
    };
    key?: string;
    assetType?: string;
    isDistributable?: boolean;
    Comment?: string;
    specifiedPipelines?: string[];
    previewLocation?: {
        Key?: string;
    };
    Asset?: FileUploadTableItem[];
    DirectoryHandle?: any;
    Preview?: File;
}

type UpdateAssetIdAction = {
    type: "UPDATE_ASSET_ID";
    payload: string;
};

type UpdateAssetDatabaseAction = {
    type: "UPDATE_ASSET_DATABASE";
    payload: string;
};

type UpdateAssetDistributableAction = {
    type: "UPDATE_ASSET_DISTRIBUTABLE";
    payload: boolean;
};

type UpdateAssetDescription = {
    type: "UPDATE_ASSET_DESCRIPTION";
    payload: string;
};
type UpdateAssetTags = {
    type: "UPDATE_ASSET_TAGS";
    payload: string[];
};
type UpdateAssetFrontendTags = {
    type: "UPDATE_ASSET_FRONTEND_TAGS";
    payload: { label: string; value: string }[];
};
type UpdateAssetLinksFe = {
    type: "UPDATE_ASSET_LINKS_FE";
    payload: {
        parents: string[];
        child: string[];
        related: string[];
    };
};
type UpdateAssetLinks = {
    type: "UPDATE_ASSET_LINKs";
    payload: {
        parents: string[];
        child: string[];
        related: string[];
    };
};

type UpdateAssetComment = {
    type: "UPDATE_ASSET_COMMENT";
    payload: string;
};

type UpdateAssetType = {
    type: "UPDATE_ASSET_TYPE";
    payload: string;
};

type UpdateAssetPipelines = {
    type: "UPDATE_ASSET_PIPELINES";
    payload: string[];
};

type UpdateAssetPreviewLocation = {
    type: "UPDATE_ASSET_PREVIEW_LOCATION";
    payload: {
        Key?: string;
    };
};

type UpdateAssetPreview = {
    type: "UPDATE_ASSET_PREVIEW";
    payload: File;
};

type UpdateAssetDirectoryHandle = {
    type: "UPDATE_ASSET_DIRECTORY_HANDLE";
    payload: any;
};

type UpdateAssetFiles = {
    type: "UPDATE_ASSET_FILES";
    payload: FileUploadTableItem[];
};

type UpdateAssetName = {
    type: "UPDATE_ASSET_NAME";
    payload: string;
};

type UpdateAssetKey = {
    type: "UPDATE_ASSET_KEY";
    payload: string;
};

type UpdateAssetIsMultiFile = {
    type: "UPDATE_ASSET_IS_MULTI_FILE";
    payload: boolean;
};

type AssetDetailAction =
    | UpdateAssetIdAction
    | UpdateAssetDatabaseAction
    | UpdateAssetDistributableAction
    | UpdateAssetDescription
    | UpdateAssetTags
    | UpdateAssetFrontendTags
    | UpdateAssetLinksFe
    | UpdateAssetLinks
    | UpdateAssetComment
    | UpdateAssetType
    | UpdateAssetPipelines
    | UpdateAssetPreviewLocation
    | UpdateAssetPreview
    | UpdateAssetDirectoryHandle
    | UpdateAssetFiles
    | UpdateAssetName
    | UpdateAssetKey
    | UpdateAssetIsMultiFile;

const assetDetailReducer = (
    assetDetailState: AssetDetail,
    assetDetailAction: AssetDetailAction
): AssetDetail => {
    switch (assetDetailAction.type) {
        case "UPDATE_ASSET_ID":
            return {
                ...assetDetailState,
                assetId: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_DATABASE":
            return {
                ...assetDetailState,
                databaseId: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_DISTRIBUTABLE":
            return {
                ...assetDetailState,
                isDistributable: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_DESCRIPTION":
            return {
                ...assetDetailState,
                description: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_TAGS":
            return {
                ...assetDetailState,
                tags: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_FRONTEND_TAGS":
            return {
                ...assetDetailState,
                frontendTags: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_LINKS_FE":
            return {
                ...assetDetailState,
                assetLinksFe: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_LINKs":
            return {
                ...assetDetailState,
                assetLinks: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_COMMENT":
            return {
                ...assetDetailState,
                Comment: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_TYPE":
            return {
                ...assetDetailState,
                assetType: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_PIPELINES":
            return {
                ...assetDetailState,
                specifiedPipelines: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_PREVIEW_LOCATION":
            return {
                ...assetDetailState,
                previewLocation: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_PREVIEW":
            return {
                ...assetDetailState,
                Preview: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_DIRECTORY_HANDLE":
            return {
                ...assetDetailState,
                DirectoryHandle: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_FILES":
            return {
                ...assetDetailState,
                Asset: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_NAME":
            return {
                ...assetDetailState,
                assetName: assetDetailAction.payload,
            };

        case "UPDATE_ASSET_KEY":
            return {
                ...assetDetailState,
                key: assetDetailAction.payload,
            };
        case "UPDATE_ASSET_IS_MULTI_FILE":
            return {
                ...assetDetailState,
                isMultiFile: assetDetailAction.payload,
            };
        default:
            return assetDetailState;
    }
};

type AssetDetailContextType = {
    assetDetailState: AssetDetail;
    assetDetailDispatch: Dispatch<AssetDetailAction>;
};

const AssetDetailContext = createContext<AssetDetailContextType | undefined>(undefined);

const isDistributableOptions: OptionDefinition[] = [
    { label: "Yes", value: "true" },
    { label: "No", value: "false" },
];

const CancelButtonModal = ({
    onDismiss,
    visible,
}: {
    onDismiss: (dismiss: boolean) => void;
    visible: boolean;
}) => {
    const navigate = useNavigate();
    return (
        <Modal
            onDismiss={() => onDismiss(false)}
            visible={visible}
            footer={
                <Box float="right">
                    <SpaceBetween direction="horizontal" size="xs">
                        <Button variant="link" onClick={() => onDismiss(false)}>
                            Cancel
                        </Button>
                        <Button variant="primary" onClick={() => navigate("/assets")}>
                            Ok
                        </Button>
                    </SpaceBetween>
                </Box>
            }
            header="Do you want to cancel"
        >
            All unsaved changes will be lost
        </Modal>
    );
};

interface AssetPrimaryInfoProps {
    setValid: (validity: boolean) => void;
    showErrors: boolean;
}
interface AssetLinkingProps {
    setValid: (validity: boolean) => void;
    showErrors: boolean;
}

const AssetPrimaryInfo = ({ setValid, showErrors }: AssetPrimaryInfoProps) => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState, assetDetailDispatch } = assetDetailContext;
    const [validationText, setValidationText] = useState<{
        assetId?: string;
        databaseId?: string;
        description?: string;
        Comment?: string;
        tags?: string;
    }>({});
    const [selectedTags, setSelectedTags] = useState<OptionDefinition[]>(
        assetDetailState.frontendTags as OptionDefinition[]
    );
    const [constraintText, setConstraintText] = useState<{
        tags?: string;
    }>({});
    const [tagsValid, setTagsValid] = useState(true);

    // Default `Comment` to an empty string so that it's optional and passes API validation
    useEffect(() => {
        if (!assetDetailState.Comment) {
            assetDetailDispatch({
                type: "UPDATE_ASSET_COMMENT",
                payload: "",
            });
        }
        if (!assetDetailState.tags) {
            assetDetailDispatch({
                type: "UPDATE_ASSET_TAGS",
                payload: [],
            });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        const validation = {
            assetId: validateNonZeroLengthTextAsYouType(assetDetailState.assetId),
            databaseId: validateNonZeroLengthTextAsYouType(assetDetailState.databaseId),
            description: validateNonZeroLengthTextAsYouType(assetDetailState.description),
            Comment: "",
            tags: validateRequiredTagTypeSelected(assetDetailState.tags, tagTypes),
        };
        setValidationText(validation);

        // Handle displaying error when tag is selected
        if (validation.tags && assetDetailState?.tags?.length) {
            // If the validation.tags string contains text and there are selected tags, it is not valid
            setTagsValid(false);
        }

        const isValid = !(
            validation.assetId ||
            validation.databaseId ||
            validation.description ||
            validation.Comment ||
            validation.tags
        );
        setValid(isValid);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [
        assetDetailState.Comment,
        assetDetailState.assetId,
        assetDetailState.databaseId,
        assetDetailState.description,
        assetDetailState.tags,
    ]);

    useEffect(() => {
        // Get Tag Types to enforce when they are required
        tagTypes = [];

        fetchtagTypes().then((res) => {
            tagTypes = res;

            if (tagTypes.length) {
                const requiredTagTypes = tagTypes.filter((tagType) => tagType.required === "True");

                if (requiredTagTypes.length) {
                    // Set constraint text if there are required tag types
                    setConstraintText({
                        tags:
                            "The following tag types, listed in parentheses, require at least one selection: " +
                            requiredTagTypes.map((tagType) => tagType.tagTypeName).join(", "),
                    });

                    // Set initial validation text
                    setValidationText({
                        ...validationText,
                        tags: validateRequiredTagTypeSelected(assetDetailState.tags, tagTypes),
                    });
                }
            }
        });
    }, []);

    return (
        <Container header={<Header variant="h2">{Synonyms.Asset} Details</Header>}>
            <SpaceBetween direction="vertical" size="l">
                <FormField
                    label={`${Synonyms.Asset} Name`}
                    errorText={showErrors && validationText.assetId}
                >
                    <Input
                        value={assetDetailState.assetId || ""}
                        data-testid="assetid-input"
                        onChange={(e) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_ID",
                                payload: e.detail.value,
                            });
                        }}
                    />
                </FormField>

                <FormField label="Is Distributable?">
                    <Select
                        options={isDistributableOptions}
                        selectedOption={
                            isDistributableOptions
                                .filter(
                                    (o) =>
                                        (assetDetailState.isDistributable === false
                                            ? "No"
                                            : "Yes") === o.label
                                )
                                .pop() || isDistributableOptions[0]
                        }
                        onChange={({ detail }) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_DISTRIBUTABLE",
                                payload: detail.selectedOption.label === "Yes",
                            });
                        }}
                        filteringType="auto"
                        selectedAriaLabel="Selected"
                        data-testid="isDistributable-select"
                    />
                </FormField>

                <FormField
                    label={Synonyms.Database}
                    errorText={showErrors && validationText.databaseId}
                >
                    <DatabaseSelector
                        onChange={(x: any) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_DATABASE",
                                payload: x.detail.selectedOption.value,
                            });
                        }}
                        selectedOption={{
                            label: assetDetailState.databaseId,
                            value: assetDetailState.databaseId,
                        }}
                        data-testid="database-selector"
                    />
                </FormField>

                <FormField
                    label="Description"
                    constraintText="Minimum 4 characters"
                    errorText={showErrors && validationText.description}
                >
                    <Textarea
                        value={assetDetailState.description || ""}
                        onChange={(e) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_DESCRIPTION",
                                payload: e.detail.value,
                            });
                        }}
                        data-testid="asset-description-textarea"
                    />
                </FormField>

                <FormField
                    label="Tags"
                    constraintText={constraintText.tags}
                    errorText={showErrors || !tagsValid ? validationText.tags : null}
                >
                    <Multiselect
                        selectedOptions={selectedTags}
                        onChange={({ detail }) => {
                            assetTags = [];
                            detail.selectedOptions.forEach((x: any) => {
                                assetTags.push(x.value);
                            });
                            setSelectedTags(detail.selectedOptions as OptionDefinition[]);
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_TAGS",
                                payload: assetTags,
                            });
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_FRONTEND_TAGS",
                                payload: detail.selectedOptions as {
                                    label: string;
                                    value: string;
                                }[],
                            });
                        }}
                        placeholder="Tags"
                        options={tags}
                    />
                </FormField>

                <FormField label="Comment">
                    <Input
                        value={assetDetailState.Comment || ""}
                        onChange={(e) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_COMMENT",
                                payload: e.detail.value,
                            });
                        }}
                        data-testid="asset-comment-input"
                    />
                </FormField>
            </SpaceBetween>
        </Container>
    );
};

const AssetLinkingInfo = ({ setValid, showErrors }: AssetLinkingProps) => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const [showLinkModal, setShowLinkModal] = useState(false);
    const [selectedLinkType, setSelectedLinkType] = useState<OptionDefinition | null>(null);
    const [searchedEntity, setSearchedEntity] = useState<string | null>(null);
    const [searchResult, setSearchResult] = useState<any | null>(null);
    const { assetDetailState, assetDetailDispatch } = assetDetailContext;
    const [showTable, setShowTable] = useState(false);
    const [selectedItems, setSelectedItems] = useState<any[]>([]);

    //Enabled Features
    const config = Cache.getItem("config");
    const [useNoOpenSearch] = useState(
        config.featuresEnabled?.includes(featuresEnabled.NOOPENSEARCH)
    );

    const handleEntitySearch = async () => {
        try {
            if (searchedEntity) {
                let result;
                if (!useNoOpenSearch) {
                    //Use OpenSearch API
                    const body = {
                        tokens: [],
                        operation: "AND",
                        from: 0,
                        size: 100,
                        query: searchedEntity,
                        filters: [
                            {
                                query_string: {
                                    query: '(_rectype:("asset"))',
                                },
                            },
                        ],
                    };
                    console.log("body", body);
                    result = await API.post("api", "search", {
                        "Content-type": "application/json",
                        body: body,
                    });
                    result = result?.hits?.hits;
                } else {
                    //Use assets API
                    result = await fetchAllAssets();
                    result = result?.filter(
                        (item: any) => item.databaseId.indexOf("#deleted") === -1
                    );
                    result = result?.filter((item: any) =>
                        item.assetName.toLowerCase().includes(searchedEntity.toLowerCase())
                    );
                }

                if (result && Object.keys(result).length > 0) {
                    setSearchResult(result);
                } else {
                    setSearchResult(null);
                }
                setShowTable(true);
            }
        } catch (error) {
            console.error("Error fetching data:", error);
        }
    };

    const [selectedAssets, setSelectedAssets] = useState<{
        parents: any[];
        child: any[];
        related: any[];
    }>({
        parents: assetDetailState.assetLinksFe?.parents || [],
        child: assetDetailState.assetLinksFe?.child || [],
        related: assetDetailState.assetLinksFe?.related || [],
    });
    const [selectedAsset, setSelectedAsset] = useState<{
        parents: any[];
        child: any[];
        related: any[];
    }>({
        parents: assetDetailState.assetLinks?.parents || [],
        child: assetDetailState.assetLinks?.child || [],
        related: assetDetailState.assetLinks?.related || [],
    });

    const deleteLink = (linkType: string, asset: any) => {
        switch (linkType) {
            case "parent":
                setSelectedAssets((prevSelectedAssets) => ({
                    ...prevSelectedAssets,
                    parents: prevSelectedAssets.parents.filter(
                        (parent) => parent.assetId !== asset.assetId
                    ),
                }));
                setSelectedAsset((prevSelectedAssets) => ({
                    ...prevSelectedAssets,
                    parents: prevSelectedAssets.parents.filter(
                        (assetId) => assetId !== asset.assetId
                    ),
                }));
                break;
            case "child":
                setSelectedAssets((prevSelectedAssets) => ({
                    ...prevSelectedAssets,
                    child: prevSelectedAssets.child.filter(
                        (child) => child.assetId !== asset.assetId
                    ),
                }));
                setSelectedAsset((prevSelectedAssets) => ({
                    ...prevSelectedAssets,
                    child: prevSelectedAssets.child.filter((assetId) => assetId !== asset.assetId),
                }));
                break;
            case "related":
                setSelectedAssets((prevSelectedAssets) => ({
                    ...prevSelectedAssets,
                    related: prevSelectedAssets.related.filter(
                        (related) => related.assetId !== asset.assetId
                    ),
                }));
                setSelectedAsset((prevSelectedAssets) => ({
                    ...prevSelectedAssets,
                    related: prevSelectedAssets.related.filter(
                        (assetId) => assetId !== asset.assetId
                    ),
                }));
                break;
            default:
                break;
        }
    };

    const assetCols = [
        {
            id: "assetId",
            header: "Asset Name",
            cell: (item: any) => (
                <Link href={`#/databases/${item.databaseName}/assets/${item.assetId}`}>
                    {item.assetName}
                </Link>
            ),
            sortingField: "name",
            isRowHeader: true,
        },
        {
            id: "databaseId",
            header: "Database Name",
            cell: (item: any) => item.databaseName,
            sortingField: "name",
            isRowHeader: true,
        },
        {
            id: "description",
            header: "Description",
            cell: (item: any) => item.description,
            sortingField: "alt",
        },
    ];

    const assetItems = Array.isArray(searchResult)
        ? !useNoOpenSearch
            ? searchResult.map((result) => ({
                  //Search API results
                  assetName: result._source.str_assetname || "",
                  databaseName: result._source.str_databaseid || "",
                  description: result._source.str_description || "",
                  assetId: result._source.str_assetid || "",
              }))
            : //FetchAllAssets API Results (No OpenSearch)
              searchResult.map((result) => ({
                  //Search API results
                  assetName: result.assetName || "",
                  databaseName: result.databaseId || "",
                  description: result.description || "",
                  assetId: result.assetId || "",
              }))
        : []; //No result

    const [parentLinks, setParentLinks] = useState<any[]>([]);
    const [childLinks, setChildLinks] = useState<any[]>([]);
    const [relatedLinks, setRelatedLinks] = useState<any[]>([]);
    const [nameErrror, setNameError] = useState("");

    useEffect(() => {
        if (!assetDetailState.assetLinksFe) {
            assetDetailDispatch({
                type: "UPDATE_ASSET_LINKS_FE",
                payload: {
                    parents: [],
                    child: [],
                    related: [],
                },
            });
        } else {
            assetDetailDispatch({
                type: "UPDATE_ASSET_LINKS_FE",
                payload: selectedAssets,
            });
        }
        if (!assetDetailState.assetLinks) {
            assetDetailDispatch({
                type: "UPDATE_ASSET_LINKs",
                payload: {
                    parents: [],
                    child: [],
                    related: [],
                },
            });
        } else {
            assetDetailDispatch({
                type: "UPDATE_ASSET_LINKs",
                payload: selectedAsset,
            });
        }
        setValid(true);
    }, [
        assetDetailState.assetLinks,
        selectedAsset,
        assetDetailState.assetLinksFe,
        selectedAssets,
        assetOptions,
        parentLinks,
    ]);
    return (
        <>
            <Modal
                visible={showLinkModal}
                onDismiss={() => {
                    setShowLinkModal(false);
                }}
                size="large"
                header={"Add Linked Assets"}
                footer={
                    <Box float="right">
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button
                                variant="link"
                                onClick={() => {
                                    setShowLinkModal(false);
                                }}
                            >
                                Cancel
                            </Button>
                            <Button
                                variant="primary"
                                onClick={() => {
                                    setNameError("");
                                    switch (selectedLinkType?.value) {
                                        case "parent":
                                            const parentHasDuplicates = selectedItems.some(
                                                (selectedItem) =>
                                                    parentLinks.find(
                                                        (link) =>
                                                            link.assetId === selectedItem.assetId
                                                    ) !== undefined
                                            );
                                            if (parentHasDuplicates) {
                                                setNameError("This is already a parent");
                                                return;
                                            }
                                            setSelectedAssets((prevSelectedAssets) => ({
                                                parents: [
                                                    ...prevSelectedAssets.parents,
                                                    {
                                                        assetName: selectedItems[0].assetName,
                                                        assetId: selectedItems[0].assetId,
                                                        databaseId: selectedItems[0].databaseName,
                                                    },
                                                ],
                                                child: prevSelectedAssets.child,
                                                related: prevSelectedAssets.related,
                                            }));
                                            setSelectedAsset((prevSelectedAssets) => ({
                                                parents: [
                                                    ...prevSelectedAssets.parents,
                                                    selectedItems[0].assetId,
                                                ],
                                                child: prevSelectedAssets.child,
                                                related: prevSelectedAssets.related,
                                            }));

                                            setParentLinks((prevLinks) => [
                                                ...prevLinks,
                                                ...selectedItems,
                                            ]);
                                            console.log(selectedItems);
                                            break;
                                        case "child":
                                            const childHasDuplicates = selectedItems.some(
                                                (selectedItem) =>
                                                    childLinks.find(
                                                        (link) =>
                                                            link.assetId === selectedItem.assetId
                                                    ) !== undefined
                                            );
                                            if (childHasDuplicates) {
                                                setNameError("This is already a child");
                                                return;
                                            }
                                            setSelectedAssets((prevSelectedAssets) => ({
                                                child: [
                                                    ...prevSelectedAssets.child,
                                                    {
                                                        assetName: selectedItems[0].assetName,
                                                        assetId: selectedItems[0].assetId,
                                                        databaseId: selectedItems[0].databaseName,
                                                    },
                                                ],
                                                parents: prevSelectedAssets.parents,
                                                related: prevSelectedAssets.related,
                                            }));
                                            setSelectedAsset((prevSelectedAssets) => ({
                                                child: [
                                                    ...prevSelectedAssets.child,
                                                    selectedItems[0].assetId,
                                                ],
                                                parents: prevSelectedAssets.parents,
                                                related: prevSelectedAssets.related,
                                            }));
                                            setChildLinks((prevLinks) => [
                                                ...prevLinks,
                                                ...selectedItems,
                                            ]);
                                            break;
                                        case "related":
                                            const relatedHasDuplicates = selectedItems.some(
                                                (selectedItem) =>
                                                    relatedLinks.find(
                                                        (link) =>
                                                            link.assetId === selectedItem.assetId
                                                    ) !== undefined
                                            );
                                            if (relatedHasDuplicates) {
                                                setNameError("This is already related");
                                                return;
                                            }
                                            setSelectedAssets((prevSelectedAssets) => ({
                                                related: [
                                                    ...prevSelectedAssets.related,
                                                    {
                                                        assetName: selectedItems[0].assetName,
                                                        assetId: selectedItems[0].assetId,
                                                        databaseId: selectedItems[0].databaseName,
                                                    },
                                                ],
                                                child: prevSelectedAssets.child,
                                                parents: prevSelectedAssets.parents,
                                            }));
                                            setSelectedAsset((prevSelectedAssets) => ({
                                                related: [
                                                    ...prevSelectedAssets.related,
                                                    selectedItems[0].assetId,
                                                ],
                                                child: prevSelectedAssets.child,
                                                parents: prevSelectedAssets.parents,
                                            }));
                                            setRelatedLinks((prevLinks) => [
                                                ...prevLinks,
                                                ...selectedItems,
                                            ]);
                                            break;
                                        default:
                                            break;
                                    }
                                    setShowLinkModal(false);
                                    setSearchedEntity("");
                                    setSelectedLinkType(null);
                                    setShowTable(false);
                                }}
                            >
                                Add Links
                            </Button>
                        </SpaceBetween>
                    </Box>
                }
            >
                <Form>
                    <SpaceBetween direction="vertical" size="l">
                        <FormField
                            label="Relationship Type"
                            constraintText="Required. Select one event type"
                        >
                            <Select
                                selectedOption={selectedLinkType}
                                placeholder="Relationship Types"
                                options={[
                                    {
                                        label: "Parent To",
                                        value: "parent",
                                    },
                                    {
                                        label: "Child To",
                                        value: "child",
                                    },
                                    {
                                        label: "Related To",
                                        value: "related",
                                    },
                                ]}
                                onChange={({ detail }) => {
                                    setSelectedLinkType(detail.selectedOption as OptionDefinition);
                                }}
                            />
                        </FormField>

                        <FormField
                            label="Entity Name"
                            constraintText="Input asset name. Press Enter to search."
                            errorText={nameErrror}
                        >
                            <Input
                                placeholder="Search"
                                type="search"
                                value={searchedEntity || ""}
                                onChange={({ detail }) => {
                                    console.log(detail.value);
                                    setSearchedEntity(detail.value);
                                    setShowTable(false);
                                    setSelectedItems([]);
                                    setNameError("");
                                }}
                                onKeyDown={({ detail }) => {
                                    if (detail.key === "Enter") {
                                        handleEntitySearch();
                                    }
                                }}
                            />
                        </FormField>
                        {showTable && (
                            <FormField label="Entity">
                                <CustomTable
                                    columns={assetCols}
                                    items={assetItems}
                                    selectedItems={selectedItems}
                                    setSelectedItems={setSelectedItems}
                                    trackBy={"assetId"}
                                />
                            </FormField>
                        )}
                    </SpaceBetween>
                </Form>
            </Modal>

            <Container
                header={
                    <Header
                        variant="h2"
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                <Button
                                    variant="primary"
                                    onClick={() => {
                                        setShowLinkModal(true);
                                    }}
                                >
                                    Add Link
                                </Button>
                            </SpaceBetween>
                        }
                    >
                        Linked {Synonyms.Asset}s
                    </Header>
                }
            >
                <Grid gridDefinition={[{ colspan: 4 }, { colspan: 4 }, { colspan: 4 }]}>
                    <Table
                        columnDefinitions={[
                            {
                                id: "assetName",
                                header: "Asset Name",
                                cell: (item) => (
                                    <Link
                                        href={`#/databases/${item.databaseId}/assets/${item.assetId}`}
                                    >
                                        {item.assetName || "-"}
                                    </Link>
                                ),
                                sortingField: "assetName",
                                isRowHeader: true,
                            },
                            {
                                id: "actions",
                                header: "",
                                cell: (item) => (
                                    <Box float="right">
                                        <Button
                                            iconName="remove"
                                            variant="icon"
                                            onClick={() => deleteLink("parent", item)}
                                        ></Button>
                                    </Box>
                                ),
                            },
                        ]}
                        items={selectedAssets.parents}
                        loadingText="Loading Assets"
                        sortingDisabled
                        empty={
                            <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
                                <SpaceBetween size="m">
                                    <b>No parent asset</b>
                                </SpaceBetween>
                            </Box>
                        }
                        header={<Header variant="h3">Parent Assets</Header>}
                    />
                    <Table
                        columnDefinitions={[
                            {
                                id: "assetName",
                                header: "Asset Name",
                                cell: (item) => (
                                    <Link
                                        href={`#/databases/${item.databaseId}/assets/${item.assetId}`}
                                    >
                                        {item.assetName || "-"}
                                    </Link>
                                ),
                                sortingField: "assetName",
                                isRowHeader: true,
                            },
                            {
                                id: "actions",
                                header: "",
                                cell: (item) => (
                                    <Box float="right">
                                        <Button
                                            iconName="remove"
                                            variant="icon"
                                            onClick={() => deleteLink("child", item)}
                                        ></Button>
                                    </Box>
                                ),
                            },
                        ]}
                        items={selectedAssets.child}
                        loadingText="Loading Assets"
                        sortingDisabled
                        empty={
                            <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
                                <SpaceBetween size="m">
                                    <b>No child asset</b>
                                </SpaceBetween>
                            </Box>
                        }
                        header={<Header variant="h3">Child Assets</Header>}
                    />
                    <Table
                        columnDefinitions={[
                            {
                                id: "assetName",
                                header: "Asset Name",
                                cell: (item) => (
                                    <Link
                                        href={`#/databases/${item.databaseId}/assets/${item.assetId}`}
                                    >
                                        {item.assetName || "-"}
                                    </Link>
                                ),
                                sortingField: "assetName",
                                isRowHeader: true,
                            },
                            {
                                id: "actions",
                                header: "",
                                cell: (item) => (
                                    <Box float="right">
                                        <Button
                                            iconName="remove"
                                            variant="icon"
                                            onClick={() => deleteLink("related", item)}
                                        ></Button>
                                    </Box>
                                ),
                            },
                        ]}
                        items={selectedAssets.related}
                        loadingText="Loading Assets"
                        sortingDisabled
                        empty={
                            <Box margin={{ vertical: "xs" }} textAlign="center" color="inherit">
                                <SpaceBetween size="m">
                                    <b>No related asset</b>
                                </SpaceBetween>
                            </Box>
                        }
                        header={<Header variant="h3">Related Assets</Header>}
                    />
                </Grid>
            </Container>
        </>
    );
};

const AssetMetadataInfo = ({
    metadata,
    setMetadata,
    showErrors,
    setValid,
}: {
    metadata: Metadata;
    setMetadata: (metadata: Metadata) => void;
    showErrors: boolean;
    setValid: (v: boolean) => void;
}) => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState } = assetDetailContext;

    return (
        <Container header={<Header variant="h2">{Synonyms.Asset} Metadata</Header>}>
            <SpaceBetween direction="vertical" size="l">
                <ControlledMetadata
                    assetId={assetDetailState.assetId || ""}
                    databaseId={assetDetailState.databaseId || ""}
                    initialState={metadata}
                    store={(databaseId, assetId, record) => {
                        return new Promise((resolve) => {
                            setMetadata(record);
                            resolve(null);
                        });
                    }}
                    showErrors={showErrors}
                    setValid={setValid}
                    data-testid="controlled-metadata-grid"
                />
            </SpaceBetween>
        </Container>
    );
};

const getFilesFromFileHandles = async (fileHandles: any[]) => {
    const fileUploadTableItems: FileUploadTableItem[] = [];
    for (let i = 0; i < fileHandles.length; i++) {
        const file = (await fileHandles[i].handle.getFile()) as File;
        fileUploadTableItems.push({
            handle: fileHandles[i].handle,
            index: i,
            name: fileHandles[i].handle.name,
            size: file.size,
            relativePath: fileHandles[i].path,
            progress: 0,
            status: "Queued",
            loaded: 0,
            total: file.size,
        });
    }
    return fileUploadTableItems;
};

const AssetFileInfo = ({
    setFileUploadTableItems,
    setValid,
    showErrors,
}: {
    setFileUploadTableItems: (fileUploadTableItems: FileUploadTableItem[]) => void;
    setValid: (v: boolean) => void;
    showErrors: boolean;
}) => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState, assetDetailDispatch } = assetDetailContext;

    useEffect(() => {
        if (assetDetailState.Asset?.length && assetDetailState.Asset.length > 0) {
            setValid(true);
        } else {
            setValid(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [assetDetailState]);

    return (
        <Container header={<Header variant="h2">Select Files to Upload</Header>}>
            <>
                <FormField>
                    <Toggle
                        onChange={({ detail }) => {
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_IS_MULTI_FILE",
                                payload: detail.checked,
                            });
                        }}
                        checked={assetDetailState.isMultiFile}
                    >
                        Folder Upload?
                    </Toggle>
                </FormField>
                <Grid gridDefinition={[{ colspan: { default: 6 } }, { colspan: { default: 6 } }]}>
                    <FolderUpload
                        label={assetDetailState.isMultiFile ? "Choose Folder" : "Choose File"}
                        description={
                            assetDetailState.Asset
                                ? "Total Files to Upload " + assetDetailState.Asset.length
                                : ""
                        }
                        multiFile={assetDetailState.isMultiFile}
                        errorText={
                            (!assetDetailState.Asset && showErrors && "Asset is required") ||
                            undefined
                        }
                        onSelect={async (directoryHandle: any, fileHandles: any[]) => {
                            const files = await getFilesFromFileHandles(fileHandles);
                            setFileUploadTableItems(files);
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_DIRECTORY_HANDLE",
                                payload: directoryHandle,
                            });
                            assetDetailDispatch({ type: "UPDATE_ASSET_FILES", payload: files });
                            assetDetailDispatch({
                                type: "UPDATE_ASSET_IS_MULTI_FILE",
                                payload: files.length > 1,
                            });
                        }}
                    ></FolderUpload>

                    <FileUpload
                        label="Preview (Optional)"
                        disabled={false}
                        setFile={(file) => {
                            assetDetailDispatch({ type: "UPDATE_ASSET_PREVIEW", payload: file });
                        }}
                        fileFormats={previewFileFormatsStr}
                        file={assetDetailState.Preview}
                        data-testid="preview-file"
                    />
                </Grid>
            </>
        </Container>
    );
};

const AssetUploadReview = ({
    metadata,
    setActiveStepIndex,
}: {
    metadata: Metadata;
    setActiveStepIndex: (step: number) => void;
}) => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState } = assetDetailContext;

    return (
        <SpaceBetween size="xs">
            <Header
                variant="h3"
                actions={<Button onClick={() => setActiveStepIndex(0)}>Edit</Button>}
            >
                Review
            </Header>
            <Container header={<Header variant="h2">{Synonyms.Asset} Detail</Header>}>
                <ColumnLayout columns={2} variant="text-grid">
                    {Object.keys(assetDetailState)
                        .filter(
                            (k) =>
                                k !== "Asset" &&
                                k !== "DirectoryHandle" &&
                                k !== "frontendTags" &&
                                k !== "assetLinksFe" &&
                                k !== "assetLinks"
                        )
                        .sort()
                        .map((k) => {
                            let transformedValue = assetDetailState[k as keyof AssetDetail];
                            if (k === "tags" && assetDetailState.frontendTags) {
                                transformedValue = assetDetailState.frontendTags
                                    .map((tag: any) => tag?.label)
                                    .join(", ");
                            }

                            return <DisplayKV key={k} label={k} value={transformedValue + " "} />;
                        })}
                </ColumnLayout>
            </Container>
            <Container header={<Header variant="h2">Linked {Synonyms.Asset}s Detail</Header>}>
                <ColumnLayout columns={3} variant="text-grid">
                    {assetDetailState.assetLinksFe && (
                        <>
                            {assetDetailState.assetLinksFe &&
                                Object.keys(assetDetailState.assetLinksFe).map((linkType) => {
                                    let label = linkType;
                                    if (linkType === "parents") {
                                        label = "Parent Assets";
                                    } else if (linkType === "child") {
                                        label = "Child Assets";
                                    } else if (linkType === "related") {
                                        label = "Related Assets";
                                    }

                                    const formattedValue =
                                        assetDetailState.assetLinksFe &&
                                        assetDetailState.assetLinksFe[
                                            linkType as keyof typeof assetDetailState.assetLinksFe
                                        ];

                                    return (
                                        <DisplayKV
                                            key={linkType}
                                            label={label}
                                            value={
                                                Array.isArray(formattedValue)
                                                    ? formattedValue.map((asset) => (
                                                          <div key={asset.assetId}>
                                                              {asset.assetName}
                                                          </div>
                                                      ))
                                                    : formattedValue
                                            }
                                        />
                                    );
                                })}
                        </>
                    )}
                </ColumnLayout>
            </Container>

            <Container header={<Header variant="h2">{Synonyms.Asset} Metadata</Header>}>
                <ColumnLayout columns={2} variant="text-grid">
                    {Object.keys(metadata).map((k) => (
                        <DisplayKV key={k} label={k} value={metadata[k as keyof Metadata]} />
                    ))}
                </ColumnLayout>
            </Container>
            {assetDetailState.Asset && (
                <FileUploadTable
                    allItems={assetDetailState.Asset}
                    resume={false}
                    showCount={false}
                    columnDefinitions={[
                        {
                            id: "filepath",
                            header: "Path",
                            cell: (item: FileUploadTableItem) => item.relativePath,
                            sortingField: "filepath",
                            isRowHeader: true,
                        },
                        {
                            id: "filesize",
                            header: "Size",
                            cell: (item: FileUploadTableItem) =>
                                item.total ? shortenBytes(item.total) : "0b",
                            sortingField: "filesize",
                            isRowHeader: true,
                        },
                    ]}
                />
            )}
        </SpaceBetween>
    );
};

const UploadForm = () => {
    const assetDetailContext = useContext(AssetDetailContext) as AssetDetailContextType;
    const { assetDetailState, assetDetailDispatch } = assetDetailContext;
    const [activeStepIndex, setActiveStepIndex] = useState(0);
    const [metadata, setMetadata] = useState<Metadata>({});
    const [fileUploadTableItems, setFileUploadTableItems] = useState<FileUploadTableItem[]>([]);
    const [freezeWizardButtons, setFreezeWizardButtons] = useState(false);
    const [showUploadAndExecProgress, setShowUploadAndExecProgress] = useState(false);
    const [uploadExecutionProps, setUploadExecutionProps] = useState<UploadExecutionProps>();
    const [previewUploadProgress, setPreviewUploadProgress] = useState<ProgressBarProps>({
        value: 0,
        status: "in-progress",
    });
    const [isCancelVisible, setCancelVisible] = useState(false);
    const [showErrorsForPage, setShowErrorsForPage] = useState(-1);
    const [validSteps, setValidSteps] = useState([false, false, false]);

    useEffect(() => {
        tags = [];

        fetchTags().then((res) => {
            if (res && Array.isArray(res)) {
                Object.values(res).map((x: any) => {
                    tags.push({ label: `${x.tagName} (${x.tagTypeName})`, value: x.tagName });
                });
            }
        });
        if (assetDetailState.assetId && fileUploadTableItems.length > 0) {
            assetDetailDispatch({ type: "UPDATE_ASSET_FILES", payload: fileUploadTableItems });
            localforage
                .setItem(assetDetailState.assetId, {
                    ...assetDetailState,
                    Asset: fileUploadTableItems,
                })
                .then(() => {})
                .catch(() => {
                    console.error("Error setting item in localforage");
                });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [fileUploadTableItems]);

    const [execStatus, setExecStatus] = useState<Record<string, StatusIndicatorProps.Type>>({});

    const getUpdatedItemAfterProgress = (
        item: FileUploadTableItem,
        loaded: number,
        total: number
    ): FileUploadTableItem => {
        const progress = Math.round((loaded / total) * 100);
        const status = item.status;
        if (loaded === total) {
            return {
                ...item,
                loaded: loaded,
                total: total,
                progress: progress,
                status: "Completed",
            };
        }
        if (status === "Queued") {
            return {
                ...item,
                loaded: loaded,
                total: total,
                progress: progress,
                status: "In Progress",
                startedAt: Math.floor(new Date().getTime() / 1000),
            };
        } else {
            return {
                ...item,
                loaded: loaded,
                total: total,
                status: "In Progress",
                progress: progress,
            };
        }
    };
    const updateProgressForFileUploadItem = (index: number, loaded: number, total: number) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? getUpdatedItemAfterProgress(item, loaded, total) : item
            );
        });
    };

    const fileUploadComplete = (index: number, event: any) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? { ...item, status: "Completed", progress: 100 } : item
            );
        });
    };

    const fileUploadError = (index: number, event: any) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? { ...item, status: "Failed" } : item
            );
        });
    };
    const moveToQueued = (index: number) => {
        setFileUploadTableItems((prevState) => {
            return prevState.map((item) =>
                item.index === index ? { ...item, status: "Queued" } : item
            );
        });
    };

    return (
        <Box padding={{ left: "l", right: "l" }}>
            {isCancelVisible && (
                <CancelButtonModal onDismiss={setCancelVisible} visible={isCancelVisible} />
            )}
            {showUploadAndExecProgress && uploadExecutionProps && (
                <>
                    <ProgressScreen
                        assetDetail={assetDetailState}
                        execStatus={execStatus}
                        previewUploadProgress={previewUploadProgress}
                        allFileUploadItems={fileUploadTableItems}
                        onRetry={() => onUploadRetry(uploadExecutionProps)}
                    />
                </>
            )}
            {!showUploadAndExecProgress && (
                <Wizard
                    i18nStrings={{
                        stepNumberLabel: (stepNumber) => `Step ${stepNumber}`,
                        collapsedStepsLabel: (stepNumber, stepsCount) =>
                            `Step ${stepNumber} of ${stepsCount}`,
                        skipToButtonLabel: (step, stepNumber) => `Skip to ${step.title}`,
                        navigationAriaLabel: "Steps",
                        cancelButton: "Cancel",
                        previousButton: "Previous",
                        nextButton: "Next",
                        submitButton: "Upload Object",
                        optional: "optional",
                    }}
                    isLoadingNextStep={freezeWizardButtons}
                    onCancel={(event) => {
                        setCancelVisible(true);
                    }}
                    onNavigate={({ detail }) => {
                        setShowErrorsForPage(activeStepIndex);
                        if (
                            validSteps[activeStepIndex] ||
                            activeStepIndex > detail.requestedStepIndex
                        ) {
                            setActiveStepIndex(detail.requestedStepIndex);
                        }
                    }}
                    activeStepIndex={activeStepIndex}
                    onSubmit={onSubmit({
                        assetDetail: assetDetailState,
                        setFreezeWizardButtons,
                        metadata,
                        execStatus,
                        setExecStatus,
                        setShowUploadAndExecProgress,
                        moveToQueued,
                        updateProgressForFileUploadItem,
                        fileUploadComplete,
                        fileUploadError,
                        setPreviewUploadProgress,
                        setUploadExecutionProps,
                    })}
                    allowSkipTo
                    steps={[
                        {
                            title: `${Synonyms.Asset} Details`,
                            isOptional: false,
                            content: (
                                <AssetPrimaryInfo
                                    setValid={(v: boolean) => {
                                        const newValidSteps = [...validSteps];
                                        newValidSteps[0] = v;
                                        setValidSteps(newValidSteps);
                                    }}
                                    showErrors={showErrorsForPage >= 0}
                                />
                            ),
                        },
                        {
                            title: `${Synonyms.Asset} Metadata`,
                            content: (
                                <AssetMetadataInfo
                                    setValid={(v: boolean) => {
                                        const newValidSteps = [...validSteps];
                                        newValidSteps[1] = v;
                                        setValidSteps(newValidSteps);
                                    }}
                                    showErrors={showErrorsForPage >= 1}
                                    metadata={metadata}
                                    setMetadata={setMetadata}
                                />
                            ),
                            isOptional: false,
                        },
                        {
                            title: `${Synonyms.Asset} Linking`,
                            content: (
                                <AssetLinkingInfo
                                    setValid={(v: boolean) => {
                                        const newValidSteps = [...validSteps];
                                        newValidSteps[2] = v;
                                        setValidSteps(newValidSteps);
                                    }}
                                    showErrors={showErrorsForPage >= 2}
                                />
                            ),
                            isOptional: false,
                        },
                        {
                            title: "Select Files to upload",
                            content: (
                                <AssetFileInfo
                                    setFileUploadTableItems={setFileUploadTableItems}
                                    setValid={(v: boolean) => {
                                        const newValidSteps = [...validSteps];
                                        newValidSteps[3] = v;
                                        setValidSteps(newValidSteps);
                                    }}
                                    showErrors={showErrorsForPage >= 3}
                                />
                            ),
                            isOptional: false,
                        },
                        {
                            title: "Review and Upload",
                            content: (
                                <AssetUploadReview
                                    metadata={metadata}
                                    setActiveStepIndex={setActiveStepIndex}
                                />
                            ),
                        },
                    ]}
                />
            )}
        </Box>
    );
};

export default function AssetUploadPage() {
    const [state, dispatch] = useReducer(assetDetailReducer, {
        isMultiFile: false,
        isDistributable: true,
    });
    return (
        <AssetDetailContext.Provider
            value={{ assetDetailState: state, assetDetailDispatch: dispatch }}
        >
            <Box padding={{ top: false ? "s" : "m", horizontal: "l" }}>
                <Grid gridDefinition={[{ colspan: { default: 12 } }]}>
                    <div>
                        <TextContent>
                            <Header variant="h1">Create {Synonyms.Asset}</Header>
                        </TextContent>
                        <UploadForm />
                    </div>
                </Grid>
            </Box>
        </AssetDetailContext.Provider>
    );
}
