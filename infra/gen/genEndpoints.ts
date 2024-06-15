/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const https = require("https");
const fs = require("fs");

let url = "https://raw.githubusercontent.com/boto/botocore/master/botocore/data/endpoints.json";

interface IServiceInfo {
    arn: string;
    principal: string;
    hostname: string;
    fipsHostname: string;
}

interface IServices {
    key: string;
    typedName: string;
    partition: {
        [partition: string]: IServiceInfo;
    };
}

function processJson(json: any): IServices[] {
    let services: string[] = [];

    let partition: {
        [partition: string]: {
            service: {
                [key: string]: IServiceInfo;
            };
        };
    } = {};

    // Get all services
    json["partitions"].forEach((v: any) => {
        // Append sagemaker and execute-api as it's missing from endpoints
        v["services"]["sagemaker"] = {};
        v["services"]["execute-api"] = {};
        v["services"]["ecs-tasks"] = {};
        v["services"]["ecr-dkr"] = {};

        for (let s in v["services"]) {
            const element = v["services"][s];
            const name = v["partition"];

            services.push(s);

            if (!partition[name]) {
                partition[name] = {
                    service: {},
                };
            }

            let newServiceName = s,
                newPrincipalPrefix = s;

            if (s == "es") {
                //rename to opensearch
                newServiceName = "opensearch";
                newPrincipalPrefix = "opensearchservice";
            }

            partition[name].service[s] = {
                arn: `arn:${v["partition"]}:${s}:{region}:{account-id}:{resource-id}`,
                principal: `${newPrincipalPrefix}.${v["dnsSuffix"]}`,
                hostname: `${newPrincipalPrefix}.{region}.${v["dnsSuffix"]}`,
                fipsHostname: `${newPrincipalPrefix}-fips.{region}.${v["dnsSuffix"]}`,
            };
        }
    });

    const unique = [...new Set(services)].map((s): IServices => {
        let partitionsForService: {
            [partition: string]: IServiceInfo;
        } = {};

        for (const p in partition) {
            const part = partition[p];
            const partService = part.service[s];

            if (partService) {
                partitionsForService[p] = partService;
            }
        }

        return {
            key: s,
            typedName: s.replace(/[-.]/gi, "_").toUpperCase(),
            partition: partitionsForService,
        };
    });

    return unique;
}

function writeFiles(services: IServices[]) {
    let serviceLookup: {
        [key: string]: {
            [partition: string]: IServiceInfo;
        };
    } = {};

    let typeToKeyLookup: { [typedKey: string]: string } = {};

    services.forEach((v) => {
        serviceLookup[v.key] = v.partition;
        typeToKeyLookup[v.typedName] = v.key;
    });

    const serviceType = `export type SERVICE = ${services
        .map((s) => `'${s.typedName}'`)
        .sort()
        .join(" | \n\t")};`;
    const seviceKeyLookup = `export const TYPE_SERVICE_LOOKUP = ${JSON.stringify(
        typeToKeyLookup,
        null,
        3
    )};`;
    const serviceLookupInterface = `export interface IServiceInfo {     
    arn: string,
    principal: string,
    hostname: string,
    fipsHostname: string, 
};`;

    const serviceLookupOut = `export const SERVICE_LOOKUP : {[key: string] : { [partition: string]: IServiceInfo }} = ${JSON.stringify(
        serviceLookup,
        null,
        3
    )};`;

    fs.writeFileSync(
        "./lib/helper/const.ts",
        `${[serviceType, seviceKeyLookup, serviceLookupInterface, serviceLookupOut].join("\n")}`
    );
    console.log(serviceLookupOut);
    // console.log(serviceType)
}

https
    .get(url, (res: any) => {
        let body = "";

        res.on("data", (chunk: any) => {
            body += chunk;
        });

        res.on("end", () => {
            try {
                let json = JSON.parse(body);
                // do something with JSON
                const services = processJson(json);
                writeFiles(services);
            } catch (error: any) {
                console.error(error.message);
            }
        });
    })
    .on("error", (error: any) => {
        console.error(error.message);
    });
