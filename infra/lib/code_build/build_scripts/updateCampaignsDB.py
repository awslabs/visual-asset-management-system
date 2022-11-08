import os
from collections import defaultdict as ddict
from boto3.dynamodb.types import TypeSerializer
import boto3
import json
import datetime
tableName = "pg-infrastructure-CampaignStorageTable-UA3VOC663XFQ"
bucketName = "pg-infrastructure-campaignbucket-1uw8ardart3vf"
serializer=TypeSerializer()
try:
    tableName = os.environ["DEPLOYMENT_TABLE_NAME"]
    bucketName = os.environ["DEPLOYMENT_BUCKET_NAME"]
except:
    print('Failed Loading Environs')

s3 = boto3.client('s3')
ddb = boto3.client('dynamodb')
response = {
    'statusCode': 200,
    'body': '',
    'headers': {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Credentials': True,
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
    }
}

def dict_to_item(raw):
    if type(raw) is dict:
        resp = {}
        for k, v in raw.items():
            if type(v) is str:
                resp[k] = {
                    'S': v
                }
            elif type(v) is int:
                resp[k] = {
                    'I': str(v)
                }
            elif type(v) is dict:
                resp[k] = {
                    'M': dict_to_item(v)
                }
            elif type(v) is list:
                resp[k] = []
                for i in v:
                    resp[k].append(dict_to_item(i))

        return resp
    elif type(raw) is str:
        return {
            'S': raw
        }
    elif type(raw) is int:
        return {
            'I': str(raw)
        }

def put_item(data):
    # data = dict_to_item(data)
    # data=serializer.serialize(data)["M"]
    data={k:serializer.serialize(v) for k,v in data.items()}

    # data={
    #     k: serializer.serialize(v) for k,v in ForumUser
    # }
    # print(data)
    ddb.put_item(TableName=tableName, Item=data)


def genDict2(arr):
    items=ddict(dict)
    j=1

def uploadFile(path,key):
    try:
        response=s3.upload_file(path,bucketName,key)
    except:
        return False
    return True

def genDict(arr):
    items = ddict(dict)
    j = 0
    for i in arr:
        i2 = i.split("/")
        if len(i2) == 3:
            a, b, c = i2
            try:
                items[a][b]["ObjectFiles"] =[{"S3Location":i}]
            except:
                items[a] = {b: {"object": i}}
        elif len(i2) == 4:
            a, b, c, d = i2
            while True:
                try:
                    if str(j) in items[a][b][c]:
                        j = j+1
                        continue
                    else:
                        items[a][b][c][str(j)] = i
                        j = 0
                        break
                except:
                    items[a][b][c] = {str(j): i}
                    j = 0
                    break
        else:
            continue
    return dict(items)

def genDB(arr):
    items=ddict(dict)
    arr2=[i.split("/") for i in arr]
    for i in arr2:
        if len(i)==3:
            campaign,obj,loc=i
            try:
                items[campaign][obj]["object"]='/'.join(i)
            except:
                items[campaign][obj]={"object":'/'.join(i)}
        elif len(i)==4:
            campaign,obj,attribute,loc=i
            j=0
            try:
                j=str(len(items[campaign][obj][attribute]))
            except:
                j=str(0)
            try:
                items[campaign][obj][attribute][j]='/'.join(i)
            except:
                items[campaign][obj][attribute]={j:'/'.join(i)}
    return dict(items)

def getItems(bucket):
    response = s3.list_objects_v2(Bucket=bucket)
    keys = []
    for k in response['Contents']:
        if 'Size' in k:
            if k['Size'] > 0 and 'Key' in k:
                keys.append(k['Key'])
    return keys
template={
    "ID":"",
    "Configuration":[
    ],
    "EndDate": "2021-05-20",
    "Name": "",
    "StartDate": "2021-04-13",
    "Status": "OK"
}
def getItems2(path,uploading=False):
    import glob
    import uuid
    import pandas as pd
    from pandas import DataFrame as df
    imgs=[]
    objects=[]
    files=glob.glob('data/**',recursive=True)
    obj_num=0
    for f in files:
        _id="_"+uuid.uuid4().hex
        i=f.split("\\")
        i.pop(0)
        n=i[-1].split(".")
        try:
            n2=n[0]+_id+"."+n[1]
        except:
            continue
        n3="/".join(i[:-1])+"/"+n2
        i[-1]=n2
        i.append(n3)
        i.append(f)
        if "png" in n:
            objects.append(i)
            if uploading:
                uploadFile(i[-1],i[4])
        elif "glb" in n:
            objects.append(i)
            obj_num=obj_num+1
            if uploading:
                uploadFile(i[-1],i[4])
    
    x=template.copy()
    _id=uuid.uuid4().hex
    config=[None]*obj_num
    for obj in objects:
        x["ID"]=_id
        obj_name=obj[1]
        x["Name"]=obj[0]
        att_name=obj[2]
        fileName=obj[3]
        s3Loc=obj[4]
        idx=int(obj_name)-1
        c=config[idx]
        if c==None:
            c={
                "ObjectName":obj_name
            }
        if att_name not in c.keys():
            c[att_name]=[]
        c[att_name].append({
            "Filename":fileName,
            "S3Location":s3Loc
        })
        config[idx]=c
    x["Configuration"]=config
    items=dict(x)
    put_item(items)

def lambda_handler(event,context):
    main()
    return response
def main():
    arr = getItems(bucketName)
    d = genDict(arr)
    d=genDB(arr)
    k=list(d.keys())
    for i in k:
        d[i]['ID']=i
        put_item(d[i])

def main2():
    getItems2('data/campaign1',True)
    print("Completed")

if __name__ == "__main__":
    main2()
