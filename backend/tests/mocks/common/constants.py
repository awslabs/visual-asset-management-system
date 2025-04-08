STANDARD_JSON_RESPONSE = {
    "statusCode": 200,
    "headers": {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization"
    },
    "body": {}
}

PERMISSION_CONSTRAINT_FIELDS = ["object__type", "object__id", "object__owner"]
PERMISSION_CONSTRAINT_POLICY = "p, {}, {}, {}"
