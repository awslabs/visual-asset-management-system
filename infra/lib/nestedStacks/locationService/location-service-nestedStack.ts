/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { aws_location, Stack, NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";

type LocationServiceConstructProps = cdk.StackProps;

export class LocationServiceNestedStack extends NestedStack {
    map: aws_location.CfnMap;
    mapStreets: aws_location.CfnMap;

    constructor(scope: Construct, id: string, props: LocationServiceConstructProps) {
        super(scope, id);

        // const ssmLocationServiceArn = new ssm.StringParameter(this, "ssmLocationServiceArn", {
        //     parameterName: `/${props.region}/location-service/arn`,
        //     stringValue: props.role.roleArn,
        //     type: ssm.ParameterType.STRING,
        //     allowedPattern: ".*",
        //     description: "Location Service Arn",
        // });

        // const location = new loc.PlaceIndex(scope, "PlaceIndex", {
        //     // placeIndexName: 'MyPlaceIndex', // optional, defaults to a generated name
        //     // dataSource: location.DataSource.HERE, // optional, defaults to Esri
        // });

        // location.grant(role);
        // location.grantSearch(role);

        const cfnMap = new aws_location.CfnMap(scope, "MyCfnMapRaster", {
            configuration: {
                style: "RasterEsriImagery",
            },
            mapName: `vams-map-raster-${Stack.of(scope).region}-${Stack.of(scope).stackName}`,
        });
        const cfnMap_streets = new aws_location.CfnMap(scope, "MyCfnMapStreets", {
            configuration: {
                style: "VectorEsriStreets",
            },
            mapName: `vams-map-streets-${Stack.of(scope).region}-${Stack.of(scope).stackName}`,
        });

        // const cfnIndex = new aws_location.CfnPlaceIndex(scope, "MyCfnPlaceIndex", {
        //     dataSource: "Esri",
        //     indexName: "vams-index",
        // });

        // role.addToPolicy(
        //     new iam.PolicyStatement({
        //         effect: iam.Effect.ALLOW,
        //         actions: [
        //             "geo:SearchPlaceIndexForPosition",
        //             "geo:SearchPlaceIndexForText",
        //             "geo:SearchPlaceIndexForSuggestions",
        //             "geo:GetPlace",
        //         ],
        //         resources: [cfnIndex.attrArn],
        //     })
        // );

        // make cfn outputs

        // new CfnOutput(this, "LocationServiceArn", {
        //     value: location.placeIndexArn,
        //     description: "Location Service Arn",
        //     exportName: "LocationServiceArn",
        // });
        // new CfnOutput(this, "LocationServiceIndexName", {
        //     value: location.placeIndexName,
        //     description: "Location Service Index Name",
        //     exportName: "LocationServiceIndexName",
        // });
        // new CfnOutput(this, "LocationServiceIndexArn", {
        //     value: location.placeIndexArn,
        //     description: "Location Service Index Arn",
        //     exportName: "LocationServiceIndexArn",
        // });
        // new CfnOutput(this, "MapArn", {
        //     value: cfnMap.attrArn,
        //     description: "Map Arn",
        //     exportName: "MapArn",
        // });

        this.map = cfnMap;
        this.mapStreets = cfnMap_streets;
        // this.location = location;
    }

    public addMapPermissionsToRole(role: iam.Role): void {
        role.addToPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "geo:GetMapTile",
                    "geo:GetMapSprites",
                    "geo:GetMapGlyphs",
                    "geo:GetMapStyleDescriptor",
                ],
                resources: [this.map.attrArn, this.mapStreets.attrArn],
            })
        );
    }
}
