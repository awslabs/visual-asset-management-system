# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import common.core
import sys
import json

# Script to run locally to migrate data from one VAMS deployment stack to another.
# Intended to be run as part of an A/B deployment, before the new version is fully switched over to and used and the old stack is tore down. 

# Instructions in README.md

#############################################################################################################
#############################################################################################################
#############################################################################################################

configFolderLocation = './config/'

#Get python argument input for json config schema file to use
schemaInputFile = configFolderLocation + sys.argv[1]

#Get JSON configuration from schema file
with open(schemaInputFile) as f:
    data = json.load(f)

#Set configuration variables
Region_From = data['regionFrom']
Region_To = data['regionTo']
VAMS_S3Bucket_Mapping = data['VAMS_S3Bucket_Mapping']
VAMS_DynamoDBTable_Mapping = data['VAMS_DynamoDBTable_Mapping']
    
#Run Migration Logic
common.core.migrateData(VAMS_S3Bucket_Mapping, VAMS_DynamoDBTable_Mapping, Region_From, Region_To)



