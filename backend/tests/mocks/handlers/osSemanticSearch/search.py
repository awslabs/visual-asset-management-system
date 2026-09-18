# Mock module for search.search
from unittest.mock import MagicMock

def property_token_filter_to_opensearch_query(body):
    """
    Convert a property token filter to an OpenSearch query.
    This is a mock implementation that matches the behavior expected in tests.
    """
    result = {
        "query": {
            "bool": {
                "must": [],
                "filter": [],
                "should": [],
                "must_not": [],
            }
        }
    }
    
    # Add query if present
    if "query" in body:
        result["query"]["bool"]["must"].append({
            "multi_match": {
                "type": "cross_fields",
                "query": body["query"],
                "lenient": True,
            }
        })
    
    # Process tokens if present
    if "tokens" in body:
        for token in body["tokens"]:
            property_key = token.get("propertyKey", "all")
            operator = token.get("operator", "=")
            value = token.get("value", "")
            
            if property_key == "all":
                query_type = "multi_match"
                query = {
                    query_type: {
                        "type": "best_fields",
                        "query": value,
                        "lenient": True,
                    }
                }
            else:
                query_type = "match"
                query = {
                    query_type: {
                        property_key: value
                    }
                }
            
            if operator == "=":
                if body.get("operation") == "OR":
                    result["query"]["bool"]["should"].append(query)
                else:
                    result["query"]["bool"]["must"].append(query)
            elif operator == "!=":
                result["query"]["bool"]["must_not"].append(query)
    
    # Add pagination if present
    if "from" in body:
        result["from"] = body["from"]
    if "size" in body:
        result["size"] = body["size"]
        
    return result

class SearchHandler:
    """Mock SearchHandler class"""
    def __init__(self):
        self.client = MagicMock()
        
    def lambda_handler(self, event, context):
        """Mock lambda_handler method"""
        return {
            "statusCode": 200,
            "body": '{"results": [], "total": 0}'
        }
        
    def search(self, query, filters=None, sort=None, page=1, size=10):
        """Mock search method"""
        return {
            "results": [],
            "total": 0,
            "page": page,
            "size": size
        }
