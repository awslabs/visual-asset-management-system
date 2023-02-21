# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import unittest
import urllib3
import json
import time
from subprocess import Popen, PIPE, DEVNULL

GET_REQ = {
    "version": "2.0",
    "routeKey": "GET /metadata/{database}/{assetId}",
    "rawPath": "/metadata/123/456",
    "rawQueryString": "",
    "headers": {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "deflate, gzip, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "authority": "example.execute-api.us-east-1.amazonaws.com",
        "host": "example.execute-api.us-east-1.amazonaws.com",
    },
    "requestContext": {
        "apiId": "example",
        "authorizer": {
            "jwt": {
                "claims": {
                 "cognito:username": "user@example.com",
                 "email": "user@example.com",
                },
            }
        },
        "domainName": "example.execute-api.us-east-1.amazonaws.com",
        "domainPrefix": "example",
        "http": {
            "method": "GET",
            "path": "/metadata/123/456",
            "protocol": "HTTP/1.1",
            "sourceIp": "x.x.x.x",
        },
        "requestId": "AE6vAj8EoAMEb5Q=",
        "routeKey": "GET /metadata/{databaseId}/{assetId}",
        "stage": "$default",
        "time": "09/Feb/2023:15:03:08 +0000",
        "timeEpoch": 1675954988528
    },
    "pathParameters": {
        "databaseId": "123",
        "assetId": "456"
    },
    "isBase64Encoded": False
}

POST_REQ = {
    "version": "2.0",
    "routeKey": "POST /metadata/{databaseId}/{assetId}",
    "rawPath": "/metadata/123/456",
    "rawQueryString": "",
    "headers": {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "deflate, gzip, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "authority": "example.execute-api.us-east-1.amazonaws.com",
        "authorization": "<redacted>",
        "content-length": "0",
        "x-forwarded-for": "x.x.x.x",
        "x-forwarded-port": "443",
        "x-forwarded-proto": "https"
    },
    "requestContext": {
        "apiId": "example",
        "authorizer": {
            "jwt": {
                "claims": {
                 "cognito:username": "user@example.com",
                 "email": "user@example.com",
                 "email_verified": "true",
                 "event_id": "72e54a47-3821-4f5c-b700-226826cfe4f5",
                 "token_use": "id"
                },
                "scopes": None
            }
        },
        "domainName": "example.execute-api.us-east-1.amazonaws.com",
        "domainPrefix": "example",
        "http": {
            "method": "POST",
            "path": "/metadata/123/456",
            "protocol": "HTTP/1.1",
            "sourceIp": "x.x.x.x",
        },
        "requestId": "AE96biaaIAMEZgg=",
        "routeKey": "POST /metadata/{databaseId}/{assetId}",
        "stage": "$default",
        "time": "09/Feb/2023:15:24:50 +0000",
        "timeEpoch": 1675956290412
    },
    "pathParameters": {
        "databaseId": "123",
        "assetId": "456"
    },
    "isBase64Encoded": False
}

PUT_REQ = {
    "version": "2.0",
    "routeKey": "PUT /metadata/{databaseId}/{assetId}",
    "rawPath": "/metadata/123/456",
    "rawQueryString": "",
    "headers": {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "deflate, gzip, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "authority": "example.execute-api.us-east-1.amazonaws.com",
        "authorization": "<redacted>",
        "content-length": "38",
        "content-type": "application/json",
        "host": "example.execute-api.us-east-1.amazonaws.com",
    },
    "requestContext": {
        "apiId": "example",
        "authorizer": {
            "jwt": {
                "claims": {
                 "cognito:username": "user@example.com",
                 "email": "user@example.com",
                 "email_verified": "true",
                 "token_use": "id"
                },
                "scopes": None
            }
        },
        "domainName": "example.execute-api.us-east-1.amazonaws.com",
        "domainPrefix": "example",
        "http": {
            "method": "PUT",
            "path": "/metadata/123/456",
            "protocol": "HTTP/1.1",
            "sourceIp": "x.x.x.x",
        },
        "routeKey": "PUT /metadata/{databaseId}/{assetId}",
        "stage": "$default",
    },
    "pathParameters": {
        "databaseId": "123",
        "assetId": "456"
    },
    "body": "{\"productId\": 123456, \"quantity\": 100}",
    "isBase64Encoded": False
}

DELETE_REQ = {
    "version": "2.0",
    "routeKey": "DELETE /metadata/{databaseId}/{assetId}",
    "rawPath": "/metadata/123/456",
    "rawQueryString": "",
    "headers": {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "deflate, gzip, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "authority": "example.execute-api.us-east-1.amazonaws.com",
        "authorization": "<redacted>",
        "content-length": "0",
    },
    "requestContext": {
        "apiId": "example",
        "authorizer": {
            "jwt": {
                "claims": {
                 "auth_time": "1675808143",
                 "cognito:username": "user@example.com",
                 "email": "user@example.com",
                 "email_verified": "true",
                },
                "scopes": None
            }
        },
        "domainName": "example.execute-api.us-east-1.amazonaws.com",
        "domainPrefix": "example",
        "http": {
            "method": "DELETE",
            "path": "/metadata/123/456",
            "protocol": "HTTP/1.1",
            "sourceIp": "x.x.x.x",
        },
        "requestId": "AE-U8jAkIAMEbog=",
        "routeKey": "DELETE /metadata/{databaseId}/{assetId}",
        "stage": "$default",
        "timeEpoch": 1675956460187
    },
    "pathParameters": {
        "databaseId": "123",
        "assetId": "456"
    },
    "isBase64Encoded": False
}

metadata = {
    "version": "1",
    "assetId": "...",
    "databaseId": "...",
    "metadata": {
        "f1": "value",
        "f2": "value",
    }
}


class DockerProcess():
    def __init__(self, module: str) -> None:
        self.module = module

    def start(self) -> None:
        self.test_process_output = open("/tmp/output.log", "wb")
        self.test_process = Popen([
            "docker", "run",
            "-e", "METADATA_STORAGE_TABLE_NAME={table}".format(
                table=os.environ['METADATA_STORAGE_TABLE_NAME']
            ),
            "-e", "AWS_REGION=us-east-1",
            "--rm",
            "-p", "9000:8080",
            "vamsmetadatatestimage",
            "backend.handlers.metadata.{module}.lambda_handler".format(
                module=self.module),
        ], stdout=self.test_process_output, stderr=self.test_process_output, close_fds=False)

        http = urllib3.PoolManager()
        secs = 0
        success = False
        while True:
            try:
                resp = http.request("GET", "http://localhost:9000", )
                time.sleep(1)
                secs += 1
                if secs > 5:
                    break
                success = True
            except:
                pass

        if not success:
            raise Exception("unable to setUp")

    def stop(self) -> None:
        self.test_process.terminate()


class TestMetadataDelete(unittest.TestCase):

    @classmethod
    def setUpClass(self):
        self.docker_process = DockerProcess("delete")
        self.docker_process.start()

    @classmethod
    def tearDownClass(self) -> None:
        self.docker_process.stop()

    def test_delete(self):
        http = urllib3.PoolManager()

        delete = DELETE_REQ.copy()
        encoded_data = json.dumps(delete).encode('utf-8')
        r = http.request(
            'DELETE',
            'http://localhost:9000/2015-03-31/functions/function/invocations',
            body=encoded_data,
            headers={'Content-Type': 'application/json'},
        )

        resp = json.loads(r.data.decode('utf-8'))
        print(resp)
        resp['body'] = json.loads(resp['body'])
        print(resp)
        assert 200 == resp['statusCode'], "expected 200 statusCode"


class TestMetadataRead(unittest.TestCase):

    @classmethod
    def setUpClass(self):
        self.docker_process = DockerProcess("read")
        self.docker_process.start()

    @classmethod
    def tearDownClass(self) -> None:
        self.docker_process.stop()

    def test_read(self):
        http = urllib3.PoolManager()

        get = GET_REQ.copy()
        get["pathParameters"] = {
            "databaseId": "doesnot",
            "assetId": "exist"
        },
        encoded_data = json.dumps(get).encode('utf-8')
        r = http.request(
            'POST',
            'http://localhost:9000/2015-03-31/functions/function/invocations',
            body=encoded_data,
            headers={'Content-Type': 'application/json'},
        )

        resp = json.loads(r.data.decode('utf-8'))
        print(resp)
        resp['body'] = json.loads(resp['body'])
        print(resp)
        assert 404 == resp['statusCode'], "expected 404 statusCode"


class TestMetadataCreateUpdate(unittest.TestCase):

    @classmethod
    def setUpClass(self):
        self.docker_process = DockerProcess("create")
        self.docker_process.start()

    @classmethod
    def tearDownClass(self) -> None:
        self.docker_process.stop()

    def test_missing_version(self):
        http = urllib3.PoolManager()

        put = PUT_REQ.copy()
        put['body'] = json.dumps({
            "metadata": {
                "blah": [
                    "this", "should", "not", "pass", "yet"
                ]
            }
        })
        encoded_data = json.dumps(put).encode('utf-8')
        r = http.request(
            'POST',
            'http://localhost:9000/2015-03-31/functions/function/invocations',
            body=encoded_data,
            headers={'Content-Type': 'application/json'},
        )

        resp = json.loads(r.data.decode('utf-8'))
        resp['body'] = json.loads(resp['body'])
        print(resp)
        assert 400 == resp['statusCode'], "expected 400 statusCode"
        assert "version field is missing" == resp['body']['error']

    def test_metadata_out_of_v1_spec(self):
        http = urllib3.PoolManager()

        put = PUT_REQ.copy()
        put['body'] = json.dumps({
            "version": "1",
            "metadata": {
                "blah": [
                    "this", "should", "not", "pass", "yet"
                ]
            }
        })
        encoded_data = json.dumps(put).encode('utf-8')
        r = http.request(
            'POST',
            'http://localhost:9000/2015-03-31/functions/function/invocations',
            body=encoded_data,
            headers={'Content-Type': 'application/json'},
        )

        resp = json.loads(r.data.decode('utf-8'))
        print(resp)
        assert 400 == resp['statusCode'], "expected 400 statusCode"

    def test_missing_body(self):
        http = urllib3.PoolManager()

        put = PUT_REQ.copy()
        del put['body']
        encoded_data = json.dumps(put).encode('utf-8')
        r = http.request(
            'POST',
            'http://localhost:9000/2015-03-31/functions/function/invocations',
            body=encoded_data,
            headers={'Content-Type': 'application/json'},
        )

        resp = json.loads(r.data.decode('utf-8'))
        print(resp)
        assert 400 == resp['statusCode'], "expected 400 statusCode"

    def test_create(self):
        http = urllib3.PoolManager()
        data = GET_REQ

        put = PUT_REQ.copy()
        put['body'] = json.dumps(metadata)
        encoded_data = json.dumps(put).encode('utf-8')
        r = http.request(
            'POST',
            'http://localhost:9000/2015-03-31/functions/function/invocations',
            body=encoded_data,
            headers={'Content-Type': 'application/json'},
        )

        resp = json.loads(r.data.decode('utf-8'))
        print(resp)
        assert 200 == resp['statusCode'], "expected 200 statusCode"
        assert isinstance(resp['body'], str), "expected body to be a string"
        resp['body'] = json.loads(resp['body'])
        assert resp['body']['status'] == "OK", "expected json body to include status: ok"
