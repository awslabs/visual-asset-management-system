#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import sys
import boto3
import csv
from boto3.dynamodb.types import TypeSerializer
from collections import defaultdict as ddict
from boto3.dynamodb.conditions import Key, Attr

import csv
import json
CampaignId = "02fbe32b-1b2b-4aad-8512-1ccbc38c5cb2"
tableName = "pg-infrastructure-CampaignStorageTable-1AJ9KGK1VW9IL"
filePath = "./data/q.csv"
outputPath = "./data/output.json"
outputPath2 = "./data/output2.json"
dynamodb = boto3.resource('dynamodb')


def getBody(obj, body, a1, a2):
    objloc = "1"
    bodyloc = "1"
    a1loc = "1"
    a2loc = "1"
    b={
        "object_location": 'public/'+objloc,
        "body_location": "public/"+bodyloc,
        "attrib1_location": "public/"+a1loc,
        "attrib2_location": "public/"+a2loc
    }
    return b
#0,3,1,1,2,4,4
#Test Version, Test Question, Test Placement, Object Model, Body Image, Attribute 1, Attribute 2 
#Get S3Locations of files based on index
templateJson={
    "TestQuestions":{
        "0":
            "3":{
                "0":{},
                "1":{
                    "object_location": 1 s3Loc based on ddb index 4
                    "body_location": 2 Body File in ddb,
                    "attrib1_location":4
                    "attrib2_locatoin":4 
                },
                "2":{},
                "3":{}
            }
        }
    }
}


def processJSON(path):
    print("Processing Data")
    data = ""
    with open(path, 'r') as _file:
        data = json.load(_file)
    testing = {}
    testing=ddict(dict)
    table=dynamodb.Table(tableName)
    ke=Key('CampaignId').eq(CampaignId)
    ddb_data = table.query(
        KeyConditionExpression=ke,
        ScanIndexForward=False
    )
    print(ddb_data['Items'])
    for f in data:
        vals=[str(x) for x in list(f.values())]
        print(vals)
        print(getBody(vals[3],vals[4],vals[5],vals[6]))
        # testing[vals[0]][vals[1]][vals[2]] = getBody(vals[3],vals[4],vals[5],vals[6])
        return

def lambda_handler(event, context):
    print("Uploading Test")

def convertCSVtoJSON(_input, _output):
    rows = []
    with open(_input, 'r') as _file:
        csvReader = csv.DictReader(_file)
        [rows.append(row) for row in csvReader]
    x = json.dumps(rows)
    with open(_output, 'w') as _file:
        _file.write(x)

def main():
    print("Unit Test")
    print
    # convertCSVtoJSON(filePath, outputPath)
    processJSON(outputPath)

if __name__ == "__main__":
    main()
