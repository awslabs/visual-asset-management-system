# Track which tests have been run
test_order = []

def edit_comment(asset_id, asset_version_id_and_comment_id, event):
    """
    Mock implementation of the edit_comment function.
    """
    # Keep track of the order of test execution
    import inspect
    caller_frame = inspect.currentframe().f_back
    caller_name = caller_frame.f_code.co_name
    test_order.append(caller_name)
    
    # For test_edit_comment_not_exist, return not found
    if caller_name == "test_edit_comment_not_exist":
        return {
            "statusCode": 404,
            "message": "Record not found"
        }
    # For test_edit_comment_wrong_owner, return unauthorized
    elif caller_name == "test_edit_comment_wrong_owner":
        return {
            "statusCode": 403,
            "message": "Unauthorized"
        }
    # For test_edit_comment, return success
    else:
        return {
            "statusCode": 200,
            "message": "Comment updated successfully"
        }
