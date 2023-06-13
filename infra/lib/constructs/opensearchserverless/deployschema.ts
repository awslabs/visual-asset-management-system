import { Handler, Context, Callback } from "aws-lambda";
import * as aws4 from "aws4";
import {
    BatchGetCollectionCommand,
    OpenSearchServerlessClient,
} from "@aws-sdk/client-opensearchserverless";
import { Client, Connection } from "@opensearch-project/opensearch";

export const handler: Handler = async function (event: any) {
    console.log("the event", event);

    const collectionName = event?.ResourceProperties?.collectionName;
    console.log("Collection Name", collectionName);

    const cmd = new BatchGetCollectionCommand({
        names: [collectionName],
    });

    const aossClient = new OpenSearchServerlessClient();
    const response = await aossClient.send(cmd);

    console.log("the response", response);

    return {};

    // callback({
    //     message: "helo",

    // });
};
