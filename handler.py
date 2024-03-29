#handler.py
import runpod
import os
import requests
import mimetypes
import time
from io import BytesIO
from datetime import datetime, timedelta
import base64
import uuid
import socket

from PIL import Image

# Logging system
from logger_config import configure_root_logger
# Configure the root logger at the start 
configure_root_logger()
import logging
logger = logging.getLogger('Handler')
logger.info("This will use the root logger configuration.")

# For AWS
import boto3
from dotenv import load_dotenv

# import scheduler  # import the scheduler module
import main as main_module

# from multiprocessing import Pipe
import threading
is_server_ready = False
is_server_working = True
# Pipe communication
from lib_api import PipeManager
initial_message = 'initiated'
cls_pipe_manager = PipeManager(initial_message)

# parent_conn, child_conn = Pipe(duplex=True)

load_dotenv()

# Load environment variables
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
EXTRA_ARGS = os.getenv('ARGS')
# Constants
UPLOAD_DIR = "/workspace/ComfyUI/input"

logger.info("Starting POD - V10")

def send_prompt_to_server(prompt):
    url = 'http://localhost:3000/prompt'
    data = prompt
    headers = {'Content-type': 'application/json'}
    response = requests.post(url, headers=headers, json=data)

    # Check the response
    if response.status_code != 200:
        logger.info('Request to /prompt failed with status code:', response.status_code)
    else:
        logger.info('Response from /prompt:', response.json())

def get_file_extension_from_url(url, response):
    # Get the extension from the URL
    _, ext = os.path.splitext(url)
    if not ext:
        # If no extension in URL, guess from the Content-Type header
        content_type = response.headers.get('Content-Type')
        ext = mimetypes.guess_extension(content_type) if content_type else ''
        ext = ext if ext else '.jpg'  # Default extension if none found
    return ext

def save_image_url(image_url, image_name=None):
    def save_image(url, img_name):
        try:
            response = requests.get(url)
            response.raise_for_status()  # Will raise an HTTPError for unsuccessful status codes

            # Determine the image name
            name_to_use = img_name if img_name is not None else url.split('/')[-1]
            
            # Ensure the image has an extension
            ext = get_file_extension_from_url(url, response)
            if not os.path.splitext(name_to_use)[1]:
                name_to_use += ext

            # Save the file
            with open(os.path.join(UPLOAD_DIR, name_to_use), 'wb') as file:
                file.write(response.content)

            logger.info(f"Binary image saved successfully: {name_to_use}")
        except requests.exceptions.RequestException as e:
            logger.info(f"Error downloading image: {e}")

    if isinstance(image_url, list):
        if isinstance(image_name, list) and len(image_url) == len(image_name):
            for url, name in zip(image_url, image_name):
                save_image(url, name)
        else:
            if isinstance(image_name, list):
                logger.info("Warning: Mismatch between the number of URLs and image names. Using names extracted from URLs.")
            for url in image_url:
                save_image(url, None)
    elif isinstance(image_url, str):
        save_image(image_url, image_name)
    else:
        logger.info("Invalid image_url type. It must be a string or a list of strings.")

def save_binary_image(image_blob, image_name):
    # Decode the base64-encoded string back to binary data
    decoded_image = base64.b64decode(image_blob)

    # Use the provided image_name to save the file
    with open(os.path.join(UPLOAD_DIR, image_name), 'wb') as file:
        file.write(decoded_image)

    print(f"HANDLER: Binary image saved successfully: {image_name}")

def save_aws_image(image_bucket, image_name, image_folder_input):
    s3_client = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY_ID, 
                             aws_secret_access_key=AWS_SECRET_ACCESS_KEY)

    def download_and_save(bucket, name, image_folder_input):
        try:
            # Extract just the file name, ignoring any directories in the path
            file_name_only = os.path.basename(name)
            input_file_path = os.path.join(image_folder_input, file_name_only)
            output_file_path = os.path.join(UPLOAD_DIR, file_name_only)
            
            with open(output_file_path, 'wb') as file:
                s3_client.download_fileobj(bucket, input_file_path, file)
            print(f"HANDLER: Binary image saved successfully: {file_name_only}")
        except Exception as e:
            print(f"HANDLER: Error downloading image: {e}")

    if isinstance(image_name, list):
        for name in image_name:
            download_and_save(image_bucket, name, image_folder_input)
    elif isinstance(image_name, str):
        download_and_save(image_bucket, image_name, image_folder_input)
    else:
        print("HANDLER: Invalid image_name type. It must be a string or a list of strings.")

def handle_event(event):
    # Get Event Keys and Extra payload
    job_id = event['id']
    # print(f'HANDLER EVENT: {event}')
    prompt_data = event['input']['prompt']
    extra_data = event['input']['extra_data']
    image_type = extra_data.get('image_type')
    image_data = extra_data.get('image_data')
    image_name = extra_data.get('image_name')
    image_url = extra_data.get('image_url')
    aws_bucket = extra_data.get('aws_bucket')
    image_folder_input = extra_data.get('image_folder_input')
    image_folder_output = extra_data.get('image_folder_output')

    logger.info("Extra Data")
    logger.info(f'Extra Data')
    logger.info(f'IMAGE TYPE: {image_type}')
    logger.info(f'IMAGE NAME: {image_name}')
    logger.info(f'IMAGE DATA: {image_data}')
    logger.info(f'IMAGE URL: {image_url}')
    logger.info(f'AWS BUCKET: {aws_bucket}')
    logger.info(f'IMAGE FOLDER INPUT: {image_folder_input}')
    logger.info(f'IMAGE FOLDER OUTPUT: {image_folder_output}')

    if image_type:
        if image_type == 'url':
            save_image_url(image_url)
        elif image_type == 'binary':
            save_binary_image(image_data, image_name)
        elif image_type == 'aws':
            save_aws_image(aws_bucket, image_name, image_folder_input)
        else:
            logger.info("Invalid image type provided.")
    else:
        logger.info("No complete image data provided in the event,\nOR No Input image needed.")

    # Adding Job ID and Client ID to Extra Data Payload
    extra_data['job_id'] = job_id
    extra_data['client_id'] = extra_data.get('client_id', str(uuid.uuid4().hex))
    # print(f'HANDLER PROMPT DATA: {prompt_data}')
    # print(f'HANDLER EXTRA DATA: {extra_data}')

    prompt = {
        "prompt": prompt_data,
        "extra_data": extra_data
    }
    send_prompt_to_server(prompt)

def handler(event):
    global is_server_ready, is_server_working
    is_server_ready = False
    is_server_working = True
    initialized = False
    cold_start = True

    port = 3000

    arg_dict = {
        "listen": "0.0.0.0",
        "port": port,
        "auto_launch": True,
        "disable-cuda-malloc": True,
        "highvram": True,
    }
    logger.info(f"Passing pipe_manager with message {cls_pipe_manager.receive_message()} to Main")


    start_time = datetime.now()
    timeout = timedelta(minutes=2)  # Set timeout period here
    logger.info(f"TIMEOUT is set to {timeout}")

    if is_port_in_use(port):
        # Server is hot
        logger.info(f"Port {port} is in use.")
        cold_start = False
        is_server_ready = True
        cls_pipe_manager.send_message('ready')
        checker_proc = main_checker_start()
        checker_proc.start()
    else:
        # Server is cold
        logger.info(f"Port {port} is available.")
        main_module.main_func(arg_dict, cls_pipe_manager)

        checker_proc = main_checker_start()
        checker_proc.start()

        while not is_main_started_check():
            # print("HANDLER: waiting for server ready")
            time.sleep(.1)
            if datetime.now() - start_time > timeout:
                logger.error("Initialization took too long to complete, terminating...")
                is_server_working = False
                initialized = False
                break
        cold_start = True
        
    initialized = True
    
    if initialized and is_server_working:
        handle_event(event)

    while is_server_working_check(): # True if server running / False if server is done
        time.sleep(.1)
        if datetime.now() - start_time > timeout:
            message = "Task took too long to complete, terminating... \nCheck if you sent a big Image reference or invalid characters on prompt"
            logger.error(message)
            is_server_working = False
            break

    # Last Shudown Clean up and Reset
    logger.info(f"Reset server state vars, Job is done")
    if cold_start:
        pass
    stop_checker(checker_proc)

    is_server_ready = False
    is_server_working = True
    cls_pipe_manager.send_message('initiated')
    
    return "Job is done"

def main_checker_start():
    logger.info("Starting Checker")
    checker_proc = threading.Thread(target=main_checker, daemon=True)
    return checker_proc

def main_checker():
    global is_server_ready
    global is_server_working
    while True:
        message = cls_pipe_manager.receive_message()
        # logger.info(f"Received message: {message}")
        if message == 'ready':
            # logger.info("Signal Recv: Server is Ready!")
            is_server_ready = True
        elif message == 'shutdown':
            # logger.info("Signal Recv: Server is Done!")
            is_server_working = False
            break
        time.sleep(0.05)

def is_main_started_check():
    global is_server_ready
    return is_server_ready

def is_server_working_check():
    global is_server_working
    return is_server_working

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def stop_checker(main_checker):
    main_checker.join()  # Wait for the thread to finish
    logger.info("Main checker thread has stopped.")

runpod.serverless.start({
    "handler": handler
})