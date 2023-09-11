#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

from fileinput import filename
import json
import os
import uuid


def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the Batch application
    """

    print(f"Event: {event}")
    print(f"Context: {context}")

    extension = event.get("sourceFileExtension")

    # construct different pipeline definitions based on file type
    if extension == ".e57":
        definition = construct_pdal_definition(event)
    elif extension == ".las" or extension == ".laz":
        definition = construct_potree_definition(event)
    else:
        return {
            "error": "Unsupported file type for point cloud visualization pipeline conversion. Currently only supports E57, LAZ, and LAS."
        }

    print(f"Definition: {definition}")

    return {
        "jobName": event.get("jobName"),
        #"externalSfnTaskToken": event.get("externalSfnTaskToken"),
        "pipeline": {
            "type": definition["stages"][0]["type"],
            "definition": [json.dumps(definition)]
        },
        "status": "STARTING"
    }


def construct_pdal_definition(event) -> dict:
    file_root, extension = os.path.splitext(event.get("sourceObjectKey"))
    filename = file_root.split("/")[-1]

    pdal_stage = {
        "type": "PDAL",
        "input": {
            "bucketName": event.get("sourceBucketName"),
            "objectKey": event.get("sourceObjectKey"),
            "fileExtension": event.get("sourceFileExtension")
        },
        "output": {
            "bucketName": event.get("destinationBucketName"),
            "objectDir": event.get("destinationObjectFolderKey"),
        }
    }

    potree_stage = {
        "type": "POTREE",
        "input": {
            "bucketName": event.get("destinationBucketName"),
            "objectKey": os.path.join(event.get("destinationObjectFolderKey"), filename + ".laz"),
            "fileExtension": ".laz"
        },
        "output": {
            "bucketName": event.get("destinationBucketName"),
            "objectDir": event.get("destinationObjectFolderKey"),
        }
    }

    definition = {
        "jobName": event.get("jobName"),
        "stages": [pdal_stage, potree_stage],
    }

    return definition


def construct_potree_definition(event) -> dict:
    potree_stage = {
        "type": "POTREE",
        "input": {
            "bucketName": event.get("sourceBucketName"),
            "objectKey": event.get("sourceObjectKey"),
            "fileExtension": event.get("sourceFileExtension")
        },
        "output": {
            "bucketName": event.get("destinationBucketName"),
            "objectDir": event.get("destinationObjectFolderKey"),
        }
    }

    definition = {
        "jobName": event.get("jobName"),
        "stages": [potree_stage],
    }

    return definition
