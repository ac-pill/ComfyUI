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

import logging
logger = logging.getLogger("Lib")

load_dotenv(override=True)

# Load environment variables
# Ensure that the actual names of the environment variables are specified
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')

# Class Upload Image
class AsyncImageSender:
    def __init__(self, loop, server_url):
        self.server_url = server_url
        self.session = aiohttp.ClientSession()
        self.loop = loop
        self.s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY_ID, 
                               aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
        
    async def upload_to_s3(self, file_bytes, bucket_name, s3_object_key):
        try:
            await self.loop.run_in_executor(
                None, 
                self.s3.put_object, 
                Bucket=bucket_name, 
                Key=s3_object_key, 
                Body=file_bytes
            )
            print(f"File uploaded to '{bucket_name}' as '{s3_object_key}' successfully.")
        except Exception as e:
            print(f"Error uploading file to S3: {str(e)}")

    async def upload_file(self, filepath):
        # Open the image, convert to RGB if necessary
        with Image.open(filepath) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            with BytesIO() as buf:
                img.save(buf, format='PNG')
                data = buf.getvalue()

        # If server URL is provided, upload the image to the server
        if self.server_url:
            async with self.session.post(self.server_url, data=data) as response:
                if response.status == 200:
                    print("File uploaded successfully.")
                    # Handle response content if needed here
                else:
                    print(f'Unexpected response status: {response.status}')
                return response.status
        else:
            print("No server URL provided.")

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
    def get_proc_info(self, last_node_id, job_id, filenames=None, payload=None):
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
            procinfo['filenames'] = filenames
            procinfo['payload'] = payload
        else:
            procinfo['status'] = 'running'
            procinfo['job_id'] = job_id
            procinfo['current_node'] = current
            procinfo['percentage'] = self.get_progress_percentage()
            procinfo['total'] = self.get_total()
            procinfo['cached'] = None
        return procinfo
    
    # Post Status
    def procstat_post(self, last_node_id, job_id, filenames=None, payload=None):
        logger.info('Start ProcStat')
        if last_node_id not in self.executed_nodes:
            logger.info(f'PROCSTAT TRIGGERED: {last_node_id}')
            self.queue.put_nowait(last_node_id)
            asyncio.run_coroutine_threadsafe(self.handle_queue(job_id, filenames, payload), self.loop)
        elif last_node_id is None:
            pass

    async def handle_queue(self, job_id, filenames, payload):
        while not self.queue.empty():
            last_node_id = await self.queue.get()
            await self.a_procstat_post(last_node_id, job_id, filenames, payload)
            self.queue.task_done()

    async def a_procstat_post(self, last_node_id, job_id, filenames, payload):
            procinfo = self.get_proc_info(last_node_id, job_id, filenames, payload)
            endpoint_url = self.endpoint_url
            logger.info(f'POSTING Progress: {endpoint_url}')
            try:
                # Using the aiohttp ClientSession from existing imports
                async with aiohttp.ClientSession() as session:
                    async with session.post(f'{endpoint_url}', json=procinfo) as response:
                    # response = await session.post(f'{endpoint_url}', json=procinfo)
                        if response.status == 200:
                            response_text = await response.text()
                            logger.info(response_text)
                        else:
                            logger.info(f"Received a {response.status} status code from the endpoint.")
            except Exception as e:
                logger.info("Failed to send POST to endpoint.", traceback.format_exc())
            return web.json_response(procinfo)

## Shutdown Server
def shutdown(cls_pipe_manager, message=None):
    # Check if the class exists
    if cls_pipe_manager:
        # Send the 'shutdown' command through the class
        logger.info("Shutdown Process")
        ## Cannot delete all input files, only the uploaded by user, how to track that?
        # delete_all_input_files()
        cls_pipe_manager.send_message('shutdown')
    else:
        logger.info("Cannot shutdown because the class is not created.")

    # Print Input and Output file count
    output_directory = folder_paths.get_output_directory()
    input_directory = folder_paths.get_input_directory()
    logger.info(f'Output Folder Count: {len(os.listdir(output_directory))}')
    logger.info(f'Input Folder Count: {len(os.listdir(input_directory))}')

    return
    
## Send Executed Image to API
def send_files(message):
    logger.info(f"Function send message to BOT")
    logger.info(f"BOT MESSAGE: {message}")
    # The address of bot's server
    if (message["aws_bucket"] is not None or message["endpoint_image"] is not None):
        # Upload Files
        result = upload_file(message)
        if result:
            print("Completed Task successfully with Bot Server")
            return result
        else:
            print("Error completing Task with Bot Server")
            return False

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
            logger.info(f"Deleted file: {file_path}")
        except Exception as e:
            logger.info(f"Could not delete file {file_path}. Reason: {e}")

## Delete All Input Files
def delete_all_input_files():
    input_directory = folder_paths.get_input_directory()
    for filename in os.listdir(input_directory):
        file_path = os.path.join(input_directory, filename)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
                logger.info(f"Deleted file: {file_path}")
        except Exception as e:
            logger.info(f"Could not delete file {file_path}. Reason: {e}")

## Upload files to Task Server / Task Server - Need to serve POST and AWS scenarios
def upload_file(message):
    filenames = message['filenames']
    final_filenames = []
    # Loop over all filenames and upload each file
    logger.info(f'Filenames on UPLOAD: {filenames}')
    output = folder_paths.get_output_directory()

    # S3 Upload
    if message.get("aws_bucket"):
        logger.info("Using AWS Server")
        s3_client = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY_ID, 
                                 aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
        bucket_name = message['aws_bucket']

        folder = message.get("image_folder_output", message.get("image_folder_input"))
        

        if folder is None:
            logger.info(f'Output Folder is set to None, saving on default Gemz folder')
            folder = 'gemz'

        if not bucket_exists(s3_client, bucket_name):
            create_bucket(s3_client, bucket_name)

        for filename in filenames:
            filepath = os.path.join(output, filename)
            final_filename = f"{folder}/{message['job_id']}-{filename}"
            s3_object_key = final_filename
            try:
                s3_client.upload_file(filepath, bucket_name, s3_object_key)
                logger.info(f"File '{filepath}' uploaded to '{bucket_name}' as '{s3_object_key}' successfully.")
                final_filenames.append(final_filename)
            except Exception as e:
                logger.info(f"Error uploading file to S3: {str(e)}")

     # Endpoint Upload
    elif message.get("endpoint_image"):
        logger.info("Using image endpoint")
        for filename in filenames:
            filepath = os.path.join(output, filename)
            final_filename = filename
            upload_file_to_endpoint(message["endpoint_image"], filepath, filename, message)
            final_filenames.append(final_filename)
    
    delete_images(filenames)

    return final_filenames

def create_bucket(s3_client, name, region=None):
    try:
        if region is None:
            s3_client.create_bucket(Bucket=name)
        else:
            location = {'LocationConstraint': region}
            s3_client.create_bucket(Bucket=name, CreateBucketConfiguration=location)
        logger.info(f"Bucket {name} created.")
    except ClientError as e:
        logger.info(f"Error: {e}")
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

## Ensure deletion of files uses 'with' statement for safe file handling
def upload_file_to_endpoint(endpoint_url, filepath, filename, message):
    with open(filepath, 'rb') as file:
        multipart_data = MultipartEncoder(
            fields={
                'message': json.dumps(message),
                'file': (filename, file, 'image/png')
            }
        )
        headers = {'Content-Type': multipart_data.content_type}
        response = requests.post(endpoint_url, headers=headers, data=multipart_data)
        if response.status_code != 200:
            logger.info(f'Failed to upload file {filename}: {response.content}')
        else:
            logger.info(f'Uploaded file {filename}: {response.content}')

## Missing status with filenames
## Pipe Manager
class PipeManager:
    def __init__(self, message):
        self.message = message

    def send_message(self, message):
        self.message = message
        # logger.info(f"Message '{self.message}' sent through class.")
        return self.message

    def receive_message(self):
        # logger.info(f"Message received: {self.message}")
        return self.message