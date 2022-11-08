import os
import boto3

def main():
    try:
        _bucket=os.environ['_bucket']
        print(_bucket)
        _key=os.environ['_key']
        print(_key)
        _outputKey=os.environ['_okey']
        print
    except Exception as e:
        print(str(e))

if __name__ =="__main__":
    main()