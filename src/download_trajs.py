import boto3
from botocore import UNSIGNED
from botocore.config import Config
import os

s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))
bucket = "swe-bench-submissions"
prefix = "verified/20250522_sweagent_claude-4-sonnet-20250514/trajs/"
for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
    for obj in page.get("Contents", []):
        key = obj["Key"]
        dest = key[len(prefix):]
        os.makedirs(os.path.dirname(f"trajs/{dest}"), exist_ok=True)
        s3.download_file(bucket, key, f"trajs/{dest}")