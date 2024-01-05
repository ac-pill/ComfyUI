import sys
import os
import asyncio
import traceback
import json
from dotenv import load_dotenv
import boto3
from botocore.exceptions import ClientError

from io import BytesIO
from PIL import Image, ImageOps


import aiohttp
from aiohttp import web
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder

import nodes
import folder_paths

load_dotenv()

# Class Upload Image
class AsyncImageSender:
    def __init__(self, loop, server_url):
        self.server_url = server_url
        self.session = aiohttp.ClientSession()
        self.aws_access_key_id = os.getenv('')
        self.aws_secret_access_key = os.getenv('')
        self.loop = loop
        self.s3 = boto3.client('s3', aws_access_key_id=self.aws_access_key_id, 
                          aws_secret_access_key=self.aws_secret_access_key)
        
    def upload_to_s3(self, file_bytes, bucket_name, s3_object_key):
        
        try:
            self.s3.put_object(Bucket=bucket_name, Key=s3_object_key, Body=file_bytes)
            print(f"File uploaded to '{bucket_name}' as '{s3_object_key}' successfully.")
        except Exception as e:
            print(f"Error uploading file to S3: {str(e)}")

    async def upload_file(self, filepath):
        # Open the image, convert to RGB if necessary
        with Image.open(filepath) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            with BytesIO() as buf:
                img.save(buf, format='JPEG')
                data = buf.getvalue()

        # Send the image to the server
        # Upload to AWS S3
        bucket_name = 'your_bucket_name'  # Retrieve this from environment variables if needed
        s3_object_key = 'your_s3_object_key'  # Determine the key as needed

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.upload_to_s3, data, bucket_name, s3_object_key)

        async with self.session.post(self.server_url, data=data) as response:
            if response.status == 200:
                print("File uploaded successfully.")
                # Handle response content if needed here

                # Additional logic to handle bot's execution
                await self.notify_receiver_done()
            else:
                print(f'Unexpected response status: {response.status}')
                # Handle error response here
            return response.status

    async def notify_receiver(self, message):
        async with self.session.post(self.url, data=message) as response:
            if response.status == 200:
                print('Server notified successfully.')
            else:
                print('Server notification failed.')
            return response.status

    async def delete_images(self, filepath):
        # Delete local image file after upload
        try:
            os.remove(filepath)
            print(f"Deleted image {filepath}")
        except Exception as e:
            print(f"Error deleting image {filepath}: {e}")

    async def close(self):
        await self.session.close()

    async def send_images(self, filepaths):
        for filepath in filepaths:
            upload_status = await self.upload_file(filepath)
            if upload_status == 200:
                await self.notify_receiver(f'Image {filepath} has been processed.')
                await self.delete_images(filepath)
            else:
                print(f'Error uploading file: {filepath}')
                # Retry?
        await self.session.close()

# Class Status Tracker
class NodeProgressTracker:
    def __init__(self, loop, nodes, endpoint_url):
        self.loop = loop
        self.nodes = nodes # All nodes list
        self.executed_nodes = []
        self.endpoint_url = endpoint_url
        self.queue = asyncio.Queue()

    def mark_as_executed(self, node_id):
        """Mark a node as executed."""
        if node_id not in self.executed_nodes and node_id is not None:
            self.executed_nodes.append(node_id)

    def get_progress_percentage(self):
        """Calculate and return the progress percentage."""
        executed_count = len(self.executed_nodes)
        total_count = len(self.nodes)
        return int((executed_count / total_count) * 100)
    
    def get_total(self):
        """Get total node count."""
        total_count = len(self.nodes)
        return total_count
    
    def unprocessed_nodes(self):
        """Return a list of nodes that haven't been processed."""
        return [node for node in self.nodes if node not in self.executed_nodes]

    # Send Status
    def get_proc_info(self, last_node_id, job_id, complete):
        procinfo = {}
        current = last_node_id
        if current is None:
            # Get a status completed after processed and sent
            procinfo['status'] = 'complete'
            procinfo['job_id'] = job_id
            procinfo['current_node'] = None
            procinfo['percentage'] = 100
            procinfo['total'] = self.get_total()
            procinfo['cached'] = self.unprocessed_nodes()
        else:
            procinfo['status'] = 'running'
            procinfo['job_id'] = job_id
            procinfo['current_node'] = current
            procinfo['percentage'] = self.get_progress_percentage()
            procinfo['total'] = self.get_total()
            procinfo['cached'] = None
        return procinfo
    
    # Post Status
    def procstat_post(self, last_node_id, job_id, complete):
        print('Start ProcStat')
        if last_node_id not in self.executed_nodes:
            print(f'PROCSTAT TRIGGERED: {last_node_id}')
            self.queue.put_nowait(last_node_id)
            asyncio.run_coroutine_threadsafe(self.handle_queue(job_id, complete), self.loop)
        elif last_node_id is None:
            pass

    async def handle_queue(self, job_id, complete):
        while not self.queue.empty():
            last_node_id = await self.queue.get()
            await self.a_procstat_post(last_node_id, job_id, complete)
            self.queue.task_done()

    async def a_procstat_post(self, last_node_id, job_id, complete):
            procinfo = self.get_proc_info(last_node_id, job_id, complete)
            endpoint_url = self.endpoint_url
            print(f'POSTING Progress: {endpoint_url}')
            try:
                # Using the aiohttp ClientSession from your existing imports
                async with aiohttp.ClientSession() as session:
                    response = await session.post(f'{endpoint_url}', json=procinfo)
                    if response.status == 200:
                        response_text = await response.text()
                        print(response_text)
                    else:
                        print(f"Received a {response.status} status code from the bot.")
            except Exception as e:
                print("Failed to send POST to bot.", traceback.format_exc())
            return web.json_response(procinfo)

## Shutdown Server
def shutdown(pipe, message=None):
    # Check if the pipe exists
    if pipe:
        # Send the 'shutdown' command through the pipe
        print("Shutdown Process")
        print(f"Shutdown Message: \nUsing pipe with id {id(pipe)} to send shutdown")
        delete_all_input_files()
        pipe.send('shutdown')
    else:
        print("Cannot shutdown because the pipe is not connected.")

    # Print Input and Output file count
    output_directory = folder_paths.get_output_directory()
    input_directory = folder_paths.get_input_directory()
    print(f'Output Folder Count: {len(os.listdir(output_directory))}')
    print(f'Input Folder Count: {len(os.listdir(input_directory))}')

    return
    
## Send Executed Image to API
def send_message_to_bot(user_prompt_map, prompt_id, message):
    print(f"Function send message to BOT")
    print(f"BOT MESSAGE: {message}")
    # The address of bot's server
    if (user_prompt_map[prompt_id]["endpoint_image"] is not None):
        endpoint_image = user_prompt_map[prompt_id]["endpoint_image"]
        # Upload Files
        result = upload_file(endpoint_image, message)
        if result:
            print("Completed Task successfully with Bot Server")
        else:
            print("Error completing Task with Bot Server")

## Delete Images from Server
def delete_images(filenames):
    # Ensure filenames is a list
    if isinstance(filenames, str):
        filenames = [filenames]
    # Get output folder
    output_directory = folder_paths.get_output_directory()
    # Delete files
    for filename in filenames:
        file_path = os.path.join(output_directory, filename)
        try:
            os.remove(file_path)
            print(f"Deleted file: {file_path}")
        except Exception as e:
            print(f"Could not delete file {file_path}. Reason: {e}")

## Delete All Input Files
def delete_all_input_files():
    input_directory = folder_paths.get_input_directory()
    for filename in os.listdir(input_directory):
        file_path = os.path.join(input_directory, filename)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
                print(f"Deleted file: {file_path}")
        except Exception as e:
            print(f"Could not delete file {file_path}. Reason: {e}")

## Upload files to Task Server / Task Server - Need to serve POST and AWS scenarios
def upload_file(endpoint_url, message):
    filenames = message['filenames']
    # Loop over all filenames and upload each file
    print(f'Filenames on UPLOAD: {filenames}')
    output = folder_paths.get_output_directory()

    aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
    aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')

    # Create an S3 client
    s3 = boto3.client('s3', aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key)

    bucket_name = 'gemz-bucket'  # Retrieve this from payload if needed

    if not bucket_exists(s3, bucket_name):
        create_bucket(s3, bucket_name)

    count = 1
    for filename in filenames:
        filepath = os.path.join(output, filename) 
        print(f'Uploading file: {filepath}')
        print(f'Count {count}')

        # Upload to AWS S3
        job_id = message['job_id']
        folder = message['folder']
        s3_object_key = f"{folder}/{job_id}-{filename}"

        # Upload the file to the S3 bucket
        try:
            s3.upload_file(filepath, bucket_name, s3_object_key)
            print(f"File '{filepath}' uploaded to '{bucket_name}' as '{s3_object_key}' successfully.")
        except Exception as e:
            print(f"Error uploading file: {str(e)}")

        # # Prepare the multipart/form-data payload
        # multipart_data = MultipartEncoder(
        #     fields={
        #         # This is the text part of the message
        #         'message': json.dumps(message),  # Convert the message dict to a JSON string < Is it right? should it be filename?
        #         # This is the file part of the message
        #         'file': (filename, open(filepath, 'rb'), 'image/png')
        #     }
        # )

        # url = f'{endpoint_url}'
        # # The custom headers must include the boundary string
        # headers = {
        #     'Content-Type': multipart_data.content_type,
        # }
        # response = requests.post(url, headers=headers, data=multipart_data)
        # if response.status_code != 200:
        #     print(f'Failed to upload file {filename}: {response.content}')
        #     delete_images(filename)
        # else:
        #     print(f'Uploaded file {filename}: {response.content}')
        #     delete_images(filename)

        count += 1
    return True

def create_bucket(s3_client, name, region=None):
    try:
        if region is None:
            s3_client.create_bucket(Bucket=name)
        else:
            location = {'LocationConstraint': region}
            s3_client.create_bucket(Bucket=name, CreateBucketConfiguration=location)
        print(f"Bucket {name} created.")
    except ClientError as e:
        print(f"Error: {e}")
        return False
    return True

def bucket_exists(s3_client, name):
    try:
        s3_client.head_bucket(Bucket=name)
        return True
    except ClientError as e:
        # If a client error is thrown, then check if it was a 404 error.
        # If it was a 404 error, then the bucket does not exist.
        error_code = int(e.response['Error']['Code'])
        if error_code == 404:
            return False
        else:
            raise e