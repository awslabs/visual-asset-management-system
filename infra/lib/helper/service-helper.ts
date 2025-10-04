/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
// Return a class provides common patterns to build a URL, ARN, principal
import { region_info, Arn, Stack, aws_iam } from "aws-cdk-lib";
import { Config } from "../../config/config";
import { SERVICE, SERVICE_LOOKUP, TYPE_SERVICE_LOOKUP } from "./const";

let config: Config;

class ServiceFormatter {
    private useFips: boolean | undefined;

    constructor(
        private name: SERVICE,
        private regionInfo: region_info.RegionInfo,
        useFipsOverride: boolean | undefined = undefined
    ) {
        //Provide a way to override if we use FIPS for this defined service or not
        if (useFipsOverride != undefined) this.useFips = useFipsOverride;
        else this.useFips = config.app.useFips;

        if (!SERVICE_LOOKUP[TYPE_SERVICE_LOOKUP[this.name]][this.regionInfo.partition || ""]) {
            throw new Error(
                `Service ${this.name} not found in partition ${this.regionInfo.partition}`
            );
        }
    }

    private get service() {
        return SERVICE_LOOKUP[TYPE_SERVICE_LOOKUP[this.name]][this.regionInfo.partition || ""];
    }

    private replaceValues(value: string, resource?: string) {
        return value
            .replace("{region}", config.env.region || "")
            .replace("{account-id}", config.env.account || "")
            .replace("{resource-id}", resource || "");
    }

    public ARN(resource: string, resourceName?: string) {
        let arn = this.replaceValues(this.service.arn, resource);

        if (resourceName) {
            arn += `/${resourceName}`;
        }
        return arn;
    }

    //public URL() {}
    public get Endpoint() {
        return this.useFips === true
            ? this.replaceValues(this.service.fipsHostname)
            : this.replaceValues(this.service.hostname);
    }
    public get Principal() {
        return new aws_iam.ServicePrincipal(this.replaceValues(this.service.principal));
    }

    public get PrincipalString() {
        return this.replaceValues(this.service.principal);
    }
}

export function Service(
    name: SERVICE,
    useFipsOverride: boolean | undefined = undefined
): ServiceFormatter {
    const ret = new ServiceFormatter(
        name,
        region_info.RegionInfo.get(config.env.region),
        useFipsOverride
    );
    //console.log(ret.Endpoint);

    return ret;
}

export function Partition() {
    return region_info.RegionInfo.get(config.env.region).partition!;
}

export function IAMArn(name: string) {
    return {
        role: `arn:${
            region_info.RegionInfo.get(config.env.region).partition || ""
        }:iam::*:role/${name}`,

        policy: `arn:${
            region_info.RegionInfo.get(config.env.region).partition || ""
        }:iam::*:policy/${name}`,

        statemachine: `arn:${
            region_info.RegionInfo.get(config.env.region).partition || ""
        }:states:${config.env.region}:${config.env.account}:stateMachine:${name}`,

        statemachineExecution: `arn:${
            region_info.RegionInfo.get(config.env.region).partition || ""
        }:states:${config.env.region}:${config.env.account}:execution:${name}`,

        stateMachineEvents: `arn:${
            region_info.RegionInfo.get(config.env.region).partition || ""
        }:event:${config.env.region}:${
            config.env.account
        }:rule/StepFunctionsGetEventsForStepFunctionsExecutionRule`,

        lambda: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:lambda:${
            config.env.region
        }:${config.env.account}:function:${name}`,

        subnet: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:ec2:${
            config.env.region
        }:${config.env.account}:subnet/${name}`,

        vpc: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:ec2:${
            config.env.region
        }:${config.env.account}:vpc/${name}`,

        securitygroup: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:ec2:${
            config.env.region
        }:${config.env.account}:security-group/${name}`,

        ssm: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:ssm:${
            config.env.region
        }:${config.env.account}:parameter/${name}`,

        loggroup: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:logs:${
            config.env.region
        }:${config.env.account}:log-group:${name}`,

        geomap: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:geo-maps:${
            config.env.region
        }::provider/default`,

        geoapi: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:geo:${
            config.env.region
        }:${config.env.account}:api-key/${name}`
    };
}

export function SetConfig(Config: Config) {
    config = Config;
}
