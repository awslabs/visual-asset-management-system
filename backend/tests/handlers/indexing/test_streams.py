# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from decimal import Decimal
import json
from unittest.mock import Mock, call

import pytest

import copy

from backend.handlers.indexing.streams import lambda_handler, AOSSIndexAssetMetadata


example_event = {
    "Records": [
        {
            "eventID": "f33f76a1c323cd5c40ba06d06c9f84f7",
            "eventName": "MODIFY",
            "eventVersion": "1.1",
            "eventSource": "aws:dynamodb",
            "awsRegion": "us-east-1",
            "dynamodb": {
                "ApproximateCreationDateTime": 1687310394.0,
                "Keys": {
                    "assetId": {
                        "S": "x68c65799-7312-4476-882b-411a609ba670"
                    },
                    "databaseId": {
                        "S": "asdf"
                    }
                },
                "NewImage": {
                    "Boolean Example": {
                        "S": "true"
                    },
                    "Example String Name": {
                        "S": "example string value"
                    },
                    "Example Number": {
                        "N": "123"
                    },
                    "assetId": {
                        "S": "x68c65799-7312-4476-882b-411a609ba670"
                    },
                    "Business Line": {
                        "S": "Musical Instruments"
                    },
                    "Site ID": {
                        "S": "6"
                    },
                    "Date Example": {
                        "S": "2023-06-28"
                    },
                    "location": {
                        "S": "{\"loc\":[\"-91.2091897\",\"30.5611007\"],\"zoom\":\"14\",\"polygons\":{\"type\":\"FeatureCollection\",\"features\":[{\"id\":\"a5d440f5cb30db2b50ade518bac047e2\",\"type\":\"Feature\",\"properties\":{},\"geometry\":{\"coordinates\":[[[-91.2056920994444,30.565357807147976],[-91.2141893376036,30.562992834836322],[-91.21676425825763,30.559814812515683],[-91.21440391432446,30.55663668610957],[-91.2024734486266,30.559593086143792],[-91.2056920994444,30.565357807147976]]],\"type\":\"Polygon\"}}]}}"
                    },
                    "databaseId": {
                        "S": "asdf"
                    }
                },
                "SequenceNumber": "551972600000000009030779036",
                "SizeBytes": 926,
                "StreamViewType": "NEW_IMAGE"
            },
            "eventSourceARN": "arn:aws:dynamodb:us-east-1:1234123123:table/vams-dev-us-east-1-MetadataStorageTable8114119D-SVTAR5CJTH10/stream/2023-06-21T01:08:09.109"
        }
    ]
}

example_event_delete = {
    "eventID": "230ef08216a12978bfcc1d5195683492",
    "eventName": "REMOVE",
    "eventVersion": "1.1",
    "eventSource": "aws:dynamodb",
    "awsRegion": "us-east-1",
    "dynamodb": {
        "ApproximateCreationDateTime": 1687441798.0,
        "Keys": {
            "assetId": {
                "S": "bar"
            },
            "databaseId": {
                "S": "foo"
            }
        },
        "SequenceNumber": "558915800000000035170341055",
        "SizeBytes": 23,
        "StreamViewType": "NEW_IMAGE"
    },
    "eventSourceARN": "arn:aws:dynamodb:us-east-1:123123123:table/vams-dev-us-east-1-MetadataStorageTable8114119D-SVTAR5CJTH10/stream/2023-06-21T01:08:09.109"
}

example_event_delete_records = {
    "Records": [
        {
            "eventID": "230ef08216a12978bfcc1d5195683492",
            "eventName": "REMOVE",
            "eventVersion": "1.1",
            "eventSource": "aws:dynamodb",
            "awsRegion": "us-east-1",
            "dynamodb": {
                "ApproximateCreationDateTime": 1687441798.0,
                "Keys": {
                    "assetId": {
                        "S": "bar"
                    },
                    "databaseId": {
                        "S": "foo"
                    }
                },
                "SequenceNumber": "558915800000000035170341055",
                "SizeBytes": 23,
                "StreamViewType": "NEW_IMAGE"
            },
            "eventSourceARN": "arn:aws:dynamodb:us-east-1:098204178297:table/vams-dev-us-east-1-MetadataStorageTable8114119D-SVTAR5CJTH10/stream/2023-06-21T01:08:09.109"
        }
    ]
}


def test_determined_field_type():

    assert "geo_point_and_polygon" == AOSSIndexAssetMetadata._determine_field_type(
        example_event["Records"][0]["dynamodb"]["NewImage"]["location"]["S"])
    assert "json" == AOSSIndexAssetMetadata._determine_field_type(
        json.dumps({"a": "b"}))
    assert "date" == AOSSIndexAssetMetadata._determine_field_type("2023-06-28")
    assert "bool" == AOSSIndexAssetMetadata._determine_field_type("true")
    assert "bool" == AOSSIndexAssetMetadata._determine_field_type("false")
    assert "num" == AOSSIndexAssetMetadata._determine_field_type("123")
    assert "num" == AOSSIndexAssetMetadata._determine_field_type(123)
    assert "str" == AOSSIndexAssetMetadata._determine_field_type(
        "example string value")
    assert "num" == AOSSIndexAssetMetadata._determine_field_type("123")
    assert "num" == AOSSIndexAssetMetadata._determine_field_type("123.0")
    assert "str" == AOSSIndexAssetMetadata._determine_field_type(None)
    assert "str" == AOSSIndexAssetMetadata._determine_field_type("")
    assert "str" == AOSSIndexAssetMetadata._determine_field_type(
        "{would not parse as json}")


def test_determine_field_name():

    assert [("str_business_line", "Musical Instruments")] == AOSSIndexAssetMetadata._determine_field_name(
        "Business Line", "Musical Instruments")
    assert [("num_example_number", 123)] == AOSSIndexAssetMetadata._determine_field_name(
        "Example Number", "123")
    assert [("num_example_number", 123)] == AOSSIndexAssetMetadata._determine_field_name(
        "Example Number", Decimal("123"))
    assert [("num_example_number", 123.53)] == AOSSIndexAssetMetadata._determine_field_name(
        "Example Number", Decimal("123.53"))
    assert [("bool_example_boolean", True)] == AOSSIndexAssetMetadata._determine_field_name(
        "Example Boolean", "true")
    assert [("bool_example_boolean", False)] == AOSSIndexAssetMetadata._determine_field_name(
        "Example Boolean", "false")
    assert [
        ("gp_location", {
            "lon": -91.2091897,
            "lat": 30.5611007,
        }),
        ("gs_location", {
            "type": "polygon",
            "coordinates": [[
                [-91.2056920994444, 30.565357807147976], [-91.2141893376036,
                                                          30.562992834836322], [-91.21676425825763, 30.559814812515683],
                [-91.21440391432446, 30.55663668610957], [-91.2024734486266,
                                                          30.559593086143792], [-91.2056920994444, 30.565357807147976]
            ]]
        })
    ] == AOSSIndexAssetMetadata._determine_field_name("location", example_event["Records"][0]["dynamodb"]["NewImage"]["location"]["S"])


def test_deserializer():

    item = example_event["Records"][0]
    result = AOSSIndexAssetMetadata._process_item(item)
    print(result)

    assert {
        '_rectype': 'asset',
        'bool_boolean_example': True,
        'str_example_string_name': 'example string value',
        'num_example_number': 123.0,
        'str_assetid': 'x68c65799-7312-4476-882b-411a609ba670',
        'str_business_line': 'Musical Instruments',
        'num_site_id': 6,
        'date_date_example': '2023-06-28',
        'gp_location': {
            "lon": -91.2091897,
            "lat": 30.5611007,
        },
        'gs_location': {
            "type": "polygon",
            "coordinates": [[
                [-91.2056920994444, 30.565357807147976], [-91.2141893376036,
                                                          30.562992834836322], [-91.21676425825763, 30.559814812515683],
                [-91.21440391432446, 30.55663668610957], [-91.2024734486266,
                                                          30.559593086143792], [-91.2056920994444, 30.565357807147976]
            ]]
        },
        'str_databaseid': 'asdf'
    } == result


def test_deserialize_null_values():

    item = {
        'eventID': '9697e06bba16c95689f2ed60bd7a0fc0', 'eventName': 'INSERT', 'eventVersion': '1.1', 'eventSource': 'aws:dynamodb', 'awsRegion': 'us-east-1',
        'dynamodb': {
            'ApproximateCreationDateTime': 1687724501.0,
            'Keys': {
                'assetId': {'S': 'xbe3b2f2c-4338-475f-828a-2b03ca92b9ec'},
                'databaseId': {'S': 'asdf'}}, 
            'NewImage': {
                'Field With Null': {'NULL': True },
            },
            'SequenceNumber': '574239200000000012164077835',
            'SizeBytes': 738,
            'StreamViewType': 'NEW_IMAGE'
        },
        'eventSourceARN': 'arn:aws:dynamodb:us-east-1:...'
    }

    result = AOSSIndexAssetMetadata._process_item(item)

    assert result == {'_rectype': 'asset'}




def test_lambda_handler():

    lambda_handler_mock = Mock()
    index = Mock()
    lambda_handler_mock.return_value = index
    index.process_item = Mock()

    s3index_mock = Mock()
    s3index_fn = Mock(return_value=s3index_mock)

    def get_asset_fields_fn(record):
        fields = {
            "assetName": "epic story",
            "description": "a long time ago"
        }
        return record | fields

    lambda_handler(example_event, {}, index=lambda_handler_mock, s3index=s3index_fn, 
                   get_asset_fields_fn=get_asset_fields_fn)

    expected = example_event["Records"][0]
    expected['dynamodb']['NewImage'] = expected['dynamodb']['NewImage'] | { "assetName": "epic story", "description": "a long time ago" }

    lambda_handler_mock.assert_called_once()
    index.process_item.assert_called_once()
    index.process_item.assert_called_with(expected)


def test_lambda_handler_delete():

    lambda_handler_mock = Mock()
    index = Mock()
    lambda_handler_mock.return_value = index
    index.process_item = Mock()
    index.delete_item = Mock()

    lambda_handler(example_event_delete, {}, index=lambda_handler_mock)

    lambda_handler_mock.assert_called_once()
    index.process_item.assert_not_called()
    index.delete_item.assert_called_once()
    index.delete_item.assert_called_with(
        example_event_delete["dynamodb"]['Keys']['assetId']['S'])


def test_lambda_handler_delete_records():
    lambda_handler_mock = Mock()
    index = Mock()
    lambda_handler_mock.return_value = index
    index.process_item = Mock()
    index.delete_item = Mock()

    lambda_handler(example_event_delete_records, {}, index=lambda_handler_mock)

    lambda_handler_mock.assert_called_once()
    index.process_item.assert_not_called()
    index.delete_item.assert_called_once()
    index.delete_item.assert_called_with(
        example_event_delete_records['Records'][0]["dynamodb"]['Keys']['assetId']['S'])


    

    # determine duplicate identifiers in the batch, take the last one
    # for each record
    # determine the key for the record
    # for each field
    # determine the field type
    # bool_*
    # the field value is literal "true" or "false"
    # dt_*
    # the field matches a regex for yyyy-mm-dd
    # num_*
    # the field is only numeric values
    # gp_*
    # the field is a GeoJSON point
    # gs_*
    # the field is a GeoJSON shape
    # str_*
    # otherwise, the field is a string
    # determine the field name
    # => field type + lower case + spaces converted to underscores

    # print(example_event.get("Records"))

    # if "Records" in example_event:
    #     for record in example_event.get("Records"):
    #         print(record)
    #         if "dynamodb" in record:
    #             print(record.get("dynamodb"))
    #             if "NewImage" in record.get("dynamodb"):
    #                 print(record.get("dynamodb").get("NewImage"))
    #                 if "location" in record.get("dynamodb").get("NewImage"):
    #                     print(record.get("dynamodb").get("NewImage").get("location"))
    #                     print(json.loads(record.get("dynamodb").get("NewImage").get("location").get("S")))
    #                     print(json.loads(record.get("dynamodb").get("NewImage").get("location").get("S")).get("polygons"))
    #                     print(json.loads(record.get("dynamodb").get("NewImage").get("location").get("S")).get("polygons").get("features"))
    #                     print(json.loads(record.get("dynamodb").get("NewImage").get("location").get("S")).get("polygons").get("features")[0].get("geometry"))

    # assert False
