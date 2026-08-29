import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv


load_dotenv()


AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")


s3 = boto3.client(
    "s3",
    region_name=AWS_REGION
)


def upload_to_s3(file_path, s3_key):

    try:

        s3.upload_file(
            file_path,
            S3_BUCKET_NAME,
            s3_key,
            ExtraArgs={
                "ContentType": "video/mp4"
            }
        )

        return True

    except (BotoCoreError, ClientError) as error:

        print("S3 upload error:", error)

        return False


def generate_video_url(s3_key):

    try:

        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": S3_BUCKET_NAME,
                "Key": s3_key
            },
            ExpiresIn=3600
        )

        return url

    except (BotoCoreError, ClientError) as error:

        print("S3 URL error:", error)

        return None


def delete_from_s3(s3_key):

    try:

        s3.delete_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key
        )

        return True

    except (BotoCoreError, ClientError) as error:

        print("S3 delete error:", error)

        return False
