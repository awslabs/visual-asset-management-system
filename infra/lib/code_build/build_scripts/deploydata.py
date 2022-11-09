#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import sys
import boto3
import csv
import json
from boto3.dynamodb.types import TypeSerializer
from collections import defaultdict as ddict

TestConfig = {
    "TestConfig": [

    ]
}
ddb=boto3.client('dynamodb')
serializer=TypeSerializer()

def put_item(data):
    data = {k: serializer.serialize(v) for k, v in data.items()}
    print(data)

def main():
    with open('data/q.csv', newline='') as f:
        reader = csv.reader(f)
        data = list(reader)
    items = ddict(dict)
    data.pop(0)
    print(data[0])
    # print(data)
    for lst in data:
        one = 'Version_'+str(lst[0])
        two = 'Task_'+str(lst[1])
        three = 'Concept_'+str(lst[2])
        _cc = {
            "Object": lst[3],
            "Att1": lst[4],
            "Att2": lst[5],
            "Body": lst[6]
        }
        if one not in items:
            items[one] = {}
        if two not in items[one]:
            items[one][two] = {}
        items[one][two][three] = _cc

    d = dict(items)

    TestConfig["TestConfig"] = d
    
    put_item(d)

    print("Deploying Data")


if __name__ == "__main__":
    main()
