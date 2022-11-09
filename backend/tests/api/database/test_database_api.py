#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import requests
from tests import AUTH_TOKEN
from tests import API_URL

database_url = API_URL + '/databases'
def test_list_database():
    print("Running test")
    result = requests.get(database_url, headers={"Authorization": AUTH_TOKEN})
    print(result.__dict__)

def test_create_database():
    requests.put(database_url, headers={"Authorization": AUTH_TOKEN}, data={

    })