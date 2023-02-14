from backend.functions.assets.uploadAssetWorkflow import add


def test_sample():
    assert 1 == 1


def test_test():
    c = add(1, 2)
    assert c == 3
