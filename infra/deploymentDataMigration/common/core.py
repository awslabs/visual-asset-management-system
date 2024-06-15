# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import boto3
import os

#Copy S3 Buckets from old stack to new stack (no region needed as S3 buckets are global named within a partition)
s3 = boto3.resource('s3')

#Function to copy individual objects between buckets
def move_s3bucket_objects(bucketFrom, bucketTo):
    src = s3.Bucket(bucketFrom)
    for archive in src.objects.all():
        s3.meta.client.copy_object(
            Bucket=bucketTo,
            CopySource={'Bucket': bucketFrom, 'Key': archive.key},
            Key=archive.key
        )

#Function to get all records from a DynamoDB table
def get_all_records_from_table(table_name, region):
    dynamodbFrom = boto3.resource('dynamodb', region_name=region)
    table = dynamodbFrom.Table(table_name)
    response = table.scan()
    records = response['Items']
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        records.extend(response['Items'])
    return records

#Function to migrate records from one DynamoDB table to another
def migrate_records_to_new_stack(records, new_stack_table_name, region):
    dynamodbTo = boto3.resource('dynamodb', region_name=region)
    table = dynamodbTo.Table(new_stack_table_name)
    for record in records:
        table.put_item(Item=record)

#Function to check if table is empty and print warning if not
def check_if_table_empty(table_name, region):
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    response = table.scan()
    if 'Items' in response and len(response['Items']) > 0:
        print("WARNING!!!!!: Table {t} is not empty, this could cause conflicts!".format(t=table_name))

#Function to migrate data from one DynamoDB table to another
def migrate_table_data(old_stack_table_name, new_stack_table_name, regionFrom, regionTo):
    #Check if new table is empty to prevent possible key collisions
    check_if_table_empty(new_stack_table_name, regionTo)
    #Get all records from old stack
    old_stack_records = get_all_records_from_table(old_stack_table_name, regionFrom)
    #Migrate records to new stack
    migrate_records_to_new_stack(old_stack_records, new_stack_table_name, regionTo)
    

def migrateData(VAMS_S3Bucket_Mapping, VAMS_DynamoDBTable_Mapping, regionFrom, regionTo):
    print(" ")
    print("-------------VAMS DATA MIGRATION SCRIPT-------------")
    print(" ")
    print("Migrating data from region {rFrom} to region {rTo}".format(rFrom=regionFrom, rTo=regionTo))
    print(" ")
    print("Bucket Configuration Data Loaded:")
    print(VAMS_S3Bucket_Mapping)
    print(" ")
    print("Table Configuration Data Loaded:")
    print(VAMS_DynamoDBTable_Mapping)
    print(" ")
    print("----------------------------------------------------")
    print(" ")

    #Map S3 bucket names from old stack to new stack and copy buckets in S3
    for item in VAMS_S3Bucket_Mapping:
        print("Migrating S3 bucket: {b}".format(b=item["bucketDescriptor"]))
        old_stack_bucket_name = item["buckets"]["from"]
        new_stack_bucket_name = item["buckets"]["to"]
        print(old_stack_bucket_name, "->", new_stack_bucket_name)
        move_s3bucket_objects(old_stack_bucket_name, new_stack_bucket_name)
        print("Finished migrating S3 bucket: {b}".format(b=item["bucketDescriptor"]))
        print(" ")

    #Map table names from old stack to new stack and migrate data in DynamoDB
    #TODO: Implement functionality to use attribute mapping inputs from old to new tables. (used for when attributes get modified/change between tables)
    for item in VAMS_DynamoDBTable_Mapping:
        print("Migrating data for table: {t}".format(t=item["tableDescriptor"]))
        old_stack_table_name = item["tables"]["from"]
        new_stack_table_name = item["tables"]["to"]
        print(old_stack_table_name, "->", new_stack_table_name)
        migrate_table_data(old_stack_table_name, new_stack_table_name, regionFrom, regionTo)
        print("Finished migrating data for table: {t}".format(t=item["tableDescriptor"]))
        print(" ")