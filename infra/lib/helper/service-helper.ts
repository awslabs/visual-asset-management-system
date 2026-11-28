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
    /**
     * The partition's DNS suffix for service hostnames (`amazonaws.com`, `amazonaws.com.cn`,
     * `amazonaws.eu`, ...), read from the hostname template after its `{region}` label so no partition
     * string is hardcoded. Throws, naming the partition, when the template is malformed or the suffix
     * is a bare public suffix (see `deriveServiceDnsSuffix`).
     */
    public get DnsSuffix() {
        return deriveServiceDnsSuffix(
            this.regionInfo.partition || "",
            this.name,
            this.service.hostname
        );
    }

    public get Principal() {
        return new aws_iam.ServicePrincipal(this.replaceValues(this.service.principal));
    }

    public get PrincipalString() {
        return this.replaceValues(this.service.principal);
    }
}

/**
 * Public suffixes (and shared registry suffixes) that must never become a CSP wildcard host. A
 * `https://*.<one of these>` source would admit every host under it. The check is a floor, not a
 * public suffix list: the real guard is that every table row ends in an AWS-controlled domain of at
 * least two labels, which `deriveServiceDnsSuffix` enforces by shape.
 */
const BARE_PUBLIC_SUFFIXES = new Set([
    "com",
    "net",
    "org",
    "edu",
    "gov",
    "mil",
    "eu",
    "uk",
    "us",
    "cn",
    "co.uk",
    "gov.uk",
    "com.cn",
    "ic.gov",
    "sgov.gov",
]);

/**
 * Derives the DNS suffix from a hostname template of the shape `<service labels>.{region}.<suffix>`.
 * Throws at synth time, naming the partition and service, when the template has no `{region}` label,
 * has nothing before it, has an empty or placeholder-bearing suffix, a suffix with fewer than two
 * labels, or a bare public suffix, so a bad table row can never produce `https://*.com`.
 */
export function deriveServiceDnsSuffix(partition: string, service: string, hostname: string) {
    const fail = (why: string): never => {
        throw new Error(
            `Partition "${partition}" service ${service}: hostname template "${hostname}" ${why}`
        );
    };
    const match = /^([a-z0-9-]+(?:\.[a-z0-9-]+)*)\.\{region\}\.(.+)$/.exec(hostname);
    if (!match) return fail("does not have the shape <service>.{region}.<suffix>");
    const suffix = match[2];
    if (/[{}*]/.test(suffix)) return fail("has a placeholder or wildcard in its DNS suffix");
    const labels = suffix.split(".");
    if (labels.some((l) => !/^[a-z0-9-]+$/.test(l))) return fail("has an empty or invalid label");
    if (labels.length < 2) return fail(`has a DNS suffix "${suffix}" with fewer than two labels`);
    if (BARE_PUBLIC_SUFFIXES.has(suffix)) return fail(`has a bare public suffix "${suffix}"`);
    return suffix;
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
        // The deployment's own account, like every other entry here. The account field was `*`, which
        // read as a cross-account grant in every policy it rendered into. iam:PassRole only ever
        // resolves a same-account role, so the effective scope was already this account and pinning it
        // changes nothing at runtime — it removes a wildcard a reviewer cannot distinguish from a real
        // cross-account grant.
        role: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:iam::${
            config.env.account
        }:role/${name}`,

        policy: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:iam::${
            config.env.account
        }:policy/${name}`,

        statemachine: `arn:${
            region_info.RegionInfo.get(config.env.region).partition || ""
        }:states:${config.env.region}:${config.env.account}:stateMachine:${name}`,

        statemachineExecution: `arn:${
            region_info.RegionInfo.get(config.env.region).partition || ""
        }:states:${config.env.region}:${config.env.account}:execution:${name}`,

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
        }:${config.env.account}:api-key/${name}`,

        sqs: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:sqs:${
            config.env.region
        }:${config.env.account}:${name}`,

        eventBus: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:events:${
            config.env.region
        }:${config.env.account}:event-bus/${name}`,

        // A rule on the default event bus (a rule on a custom bus carries the bus name in its ARN).
        eventRule: `arn:${region_info.RegionInfo.get(config.env.region).partition || ""}:events:${
            config.env.region
        }:${config.env.account}:rule/${name}`,
    };
}

export function SetConfig(Config: Config) {
    config = Config;
}
