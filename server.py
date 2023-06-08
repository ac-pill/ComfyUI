import os
import sys
import asyncio
import nodes
import folder_paths
import execution
import uuid
import json
import glob
import struct
from PIL import Image
from io import BytesIO

try:
    import aiohttp
    from aiohttp import web
except ImportError:
    print("Module 'aiohttp' not installed. Please install it via:")
    print("pip install aiohttp")
    print("or")
    print("pip install -r requirements.txt")
    sys.exit()

import mimetypes
from comfy.cli_args import args
import comfy.utils
import comfy.model_management

## Using Requests for Now, replace later with aiohttp
import requests

class BinaryEventTypes:
    PREVIEW_IMAGE = 1

async def send_socket_catch_exception(function, message):
    try:
        await function(message)
    except (aiohttp.ClientError, aiohttp.ClientPayloadError, ConnectionResetError) as err:
        print("send error:", err)

@web.middleware
async def cache_control(request: web.Request, handler):
    response: web.Response = await handler(request)
    if request.path.endswith('.js') or request.path.endswith('.css'):
        response.headers.setdefault('Cache-Control', 'no-cache')
    return response

def create_cors_middleware(allowed_origin: str):
    @web.middleware
    async def cors_middleware(request: web.Request, handler):
        if request.method == "OPTIONS":
            # Pre-flight request. Reply successfully:
            response = web.Response()
        else:
            response = await handler(request)

        response.headers['Access-Control-Allow-Origin'] = allowed_origin
        response.headers['Access-Control-Allow-Methods'] = 'POST, GET, DELETE, PUT, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response

    return cors_middleware

class PromptServer():
    def __init__(self, loop, pipe=None):
        PromptServer.instance = self

        mimetypes.init()
        mimetypes.types_map['.js'] = 'application/javascript; charset=utf-8'
        self.prompt_queue = None
        self.loop = loop
        self.messages = asyncio.Queue()
        self.number = 0

        self.args = args ## Get the args from Main

        middlewares = [cache_control]
        if self.args.enable_cors_header:
            middlewares.append(create_cors_middleware(self.args.enable_cors_header))

        self.app = web.Application(client_max_size=20971520, middlewares=middlewares)
        self.sockets = dict()
        self.web_root = os.path.join(os.path.dirname(
            os.path.realpath(__file__)), "web")
        routes = web.RouteTableDef()
        self.routes = routes
        self.last_node_id = None
        self.client_id = None
        self.user_prompt_map = {} ## To store USER related info
        self.prompt_id = 0 ## hold the prompt id on class level
        self.prompt_filenames_map = {} ## Hold the filename outputs
        self.pipe = pipe
        @routes.get('/ws')
        async def websocket_handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            sid = request.rel_url.query.get('clientId', '')
            if sid:
                # Reusing existing session, remove old
                self.sockets.pop(sid, None)
            else:
                sid = uuid.uuid4().hex

            self.sockets[sid] = ws

            try:
                # Send initial state to the new client
                await self.send("status", { "status": self.get_queue_info(), 'sid': sid }, sid)
                # On reconnect if we are the currently executing client send the current node
                if self.client_id == sid and self.last_node_id is not None:
                    await self.send("executing", { "node": self.last_node_id }, sid)
                    
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.ERROR:
                        print('ws connection closed with exception %s' % ws.exception())
            finally:
                self.sockets.pop(sid, None)
            return ws

        @routes.get("/")
        async def get_root(request):
            return web.FileResponse(os.path.join(self.web_root, "index.html"))

        @routes.get("/embeddings")
        def get_embeddings(self):
            embeddings = folder_paths.get_filename_list("embeddings")
            return web.json_response(list(map(lambda a: os.path.splitext(a)[0].lower(), embeddings)))

        @routes.get("/extensions")
        async def get_extensions(request):
            files = glob.glob(os.path.join(self.web_root, 'extensions/**/*.js'), recursive=True)
            return web.json_response(list(map(lambda f: "/" + os.path.relpath(f, self.web_root).replace("\\", "/"), files)))

        def get_dir_by_type(dir_type):
            if dir_type is None:
                dir_type = "input"

            if dir_type == "input":
                type_dir = folder_paths.get_input_directory()
            elif dir_type == "temp":
                type_dir = folder_paths.get_temp_directory()
            elif dir_type == "output":
                type_dir = folder_paths.get_output_directory()

            return type_dir, dir_type

        def image_upload(post, image_save_function=None):
            image = post.get("image")
            overwrite = post.get("overwrite")

            image_upload_type = post.get("type")
            upload_dir, image_upload_type = get_dir_by_type(image_upload_type)

            if image and image.file:
                filename = image.filename
                if not filename:
                    return web.Response(status=400)

                subfolder = post.get("subfolder", "")
                full_output_folder = os.path.join(upload_dir, os.path.normpath(subfolder))

                if os.path.commonpath((upload_dir, os.path.abspath(full_output_folder))) != upload_dir:
                    return web.Response(status=400)

                if not os.path.exists(full_output_folder):
                    os.makedirs(full_output_folder)

                split = os.path.splitext(filename)
                filepath = os.path.join(full_output_folder, filename)

                if overwrite is not None and (overwrite == "true" or overwrite == "1"):
                    pass
                else:
                    i = 1
                    while os.path.exists(filepath):
                        filename = f"{split[0]} ({i}){split[1]}"
                        filepath = os.path.join(full_output_folder, filename)
                        i += 1

                if image_save_function is not None:
                    image_save_function(image, post, filepath)
                else:
                    with open(filepath, "wb") as f:
                        f.write(image.file.read())

                return web.json_response({"name" : filename, "subfolder": subfolder, "type": image_upload_type})
            else:
                return web.Response(status=400)

        @routes.post("/upload/image")
        async def upload_image(request):
            post = await request.post()
            return image_upload(post)


        @routes.post("/upload/mask")
        async def upload_mask(request):
            post = await request.post()

            def image_save_function(image, post, filepath):
                original_ref = json.loads(post.get("original_ref"))
                filename, output_dir = folder_paths.annotated_filepath(original_ref['filename'])

                # validation for security: prevent accessing arbitrary path
                if filename[0] == '/' or '..' in filename:
                    return web.Response(status=400)

                if output_dir is None:
                    type = original_ref.get("type", "output")
                    output_dir = folder_paths.get_directory_by_type(type)

                if output_dir is None:
                    return web.Response(status=400)

                if original_ref.get("subfolder", "") != "":
                    full_output_dir = os.path.join(output_dir, original_ref["subfolder"])
                    if os.path.commonpath((os.path.abspath(full_output_dir), output_dir)) != output_dir:
                        return web.Response(status=403)
                    output_dir = full_output_dir

                file = os.path.join(output_dir, filename)

                if os.path.isfile(file):
                    with Image.open(file) as original_pil:
                        original_pil = original_pil.convert('RGBA')
                        mask_pil = Image.open(image.file).convert('RGBA')

                        # alpha copy
                        new_alpha = mask_pil.getchannel('A')
                        original_pil.putalpha(new_alpha)
                        original_pil.save(filepath, compress_level=4)

            return image_upload(post, image_save_function)

        @routes.get("/view")
        async def view_image(request):
            if "filename" in request.rel_url.query:
                filename = request.rel_url.query["filename"]
                filename,output_dir = folder_paths.annotated_filepath(filename)

                # validation for security: prevent accessing arbitrary path
                if filename[0] == '/' or '..' in filename:
                    return web.Response(status=400)

                if output_dir is None:
                    type = request.rel_url.query.get("type", "output")
                    output_dir = folder_paths.get_directory_by_type(type)

                if output_dir is None:
                    return web.Response(status=400)

                if "subfolder" in request.rel_url.query:
                    full_output_dir = os.path.join(output_dir, request.rel_url.query["subfolder"])
                    if os.path.commonpath((os.path.abspath(full_output_dir), output_dir)) != output_dir:
                        return web.Response(status=403)
                    output_dir = full_output_dir

                filename = os.path.basename(filename)
                file = os.path.join(output_dir, filename)

                if os.path.isfile(file):
                    if 'preview' in request.rel_url.query:
                        with Image.open(file) as img:
                            preview_info = request.rel_url.query['preview'].split(';')
                            image_format = preview_info[0]
                            if image_format not in ['webp', 'jpeg'] or 'a' in request.rel_url.query.get('channel', ''):
                                image_format = 'webp'

                            quality = 90
                            if preview_info[-1].isdigit():
                                quality = int(preview_info[-1])

                            buffer = BytesIO()
                            if image_format in ['jpeg'] or request.rel_url.query.get('channel', '') == 'rgb':
                                img = img.convert("RGB")
                            img.save(buffer, format=image_format, quality=quality)
                            buffer.seek(0)

                            return web.Response(body=buffer.read(), content_type=f'image/{image_format}',
                                                headers={"Content-Disposition": f"filename=\"{filename}\""})

                    if 'channel' not in request.rel_url.query:
                        channel = 'rgba'
                    else:
                        channel = request.rel_url.query["channel"]

                    if channel == 'rgb':
                        with Image.open(file) as img:
                            if img.mode == "RGBA":
                                r, g, b, a = img.split()
                                new_img = Image.merge('RGB', (r, g, b))
                            else:
                                new_img = img.convert("RGB")

                            buffer = BytesIO()
                            new_img.save(buffer, format='PNG')
                            buffer.seek(0)

                            return web.Response(body=buffer.read(), content_type='image/png',
                                                headers={"Content-Disposition": f"filename=\"{filename}\""})

                    elif channel == 'a':
                        with Image.open(file) as img:
                            if img.mode == "RGBA":
                                _, _, _, a = img.split()
                            else:
                                a = Image.new('L', img.size, 255)

                            # alpha img
                            alpha_img = Image.new('RGBA', img.size)
                            alpha_img.putalpha(a)
                            alpha_buffer = BytesIO()
                            alpha_img.save(alpha_buffer, format='PNG')
                            alpha_buffer.seek(0)

                            return web.Response(body=alpha_buffer.read(), content_type='image/png',
                                                headers={"Content-Disposition": f"filename=\"{filename}\""})
                    else:
                        return web.FileResponse(file, headers={"Content-Disposition": f"filename=\"{filename}\""})

            return web.Response(status=404)

        @routes.get("/view_metadata/{folder_name}")
        async def view_metadata(request):
            folder_name = request.match_info.get("folder_name", None)
            if folder_name is None:
                return web.Response(status=404)
            if not "filename" in request.rel_url.query:
                return web.Response(status=404)

            filename = request.rel_url.query["filename"]
            if not filename.endswith(".safetensors"):
                return web.Response(status=404)

            safetensors_path = folder_paths.get_full_path(folder_name, filename)
            if safetensors_path is None:
                return web.Response(status=404)
            out = comfy.utils.safetensors_header(safetensors_path, max_size=1024*1024)
            if out is None:
                return web.Response(status=404)
            dt = json.loads(out)
            if not "__metadata__" in dt:
                return web.Response(status=404)
            return web.json_response(dt["__metadata__"])

        @routes.get("/system_stats")
        async def get_queue(request):
            device = comfy.model_management.get_torch_device()
            device_name = comfy.model_management.get_torch_device_name(device)
            vram_total, torch_vram_total = comfy.model_management.get_total_memory(device, torch_total_too=True)
            vram_free, torch_vram_free = comfy.model_management.get_free_memory(device, torch_free_too=True)
            system_stats = {
                "devices": [
                    {
                        "name": device_name,
                        "type": device.type,
                        "index": device.index,
                        "vram_total": vram_total,
                        "vram_free": vram_free,
                        "torch_vram_total": torch_vram_total,
                        "torch_vram_free": torch_vram_free,
                    }
                ]
            }
            return web.json_response(system_stats)

        @routes.get("/prompt")
        async def get_prompt(request):
            return web.json_response(self.get_queue_info())

        def node_info(node_class):
            obj_class = nodes.NODE_CLASS_MAPPINGS[node_class]
            info = {}
            info['input'] = obj_class.INPUT_TYPES()
            info['output'] = obj_class.RETURN_TYPES
            info['output_is_list'] = obj_class.OUTPUT_IS_LIST if hasattr(obj_class, 'OUTPUT_IS_LIST') else [False] * len(obj_class.RETURN_TYPES)
            info['output_name'] = obj_class.RETURN_NAMES if hasattr(obj_class, 'RETURN_NAMES') else info['output']
            info['name'] = node_class
            info['display_name'] = nodes.NODE_DISPLAY_NAME_MAPPINGS[node_class] if node_class in nodes.NODE_DISPLAY_NAME_MAPPINGS.keys() else node_class
            info['description'] = ''
            info['category'] = 'sd'
            if hasattr(obj_class, 'OUTPUT_NODE') and obj_class.OUTPUT_NODE == True:
                info['output_node'] = True
            else:
                info['output_node'] = False

            if hasattr(obj_class, 'CATEGORY'):
                info['category'] = obj_class.CATEGORY
            return info

        @routes.get("/object_info")
        async def get_object_info(request):
            out = {}
            for x in nodes.NODE_CLASS_MAPPINGS:
                out[x] = node_info(x)
            return web.json_response(out)

        @routes.get("/object_info/{node_class}")
        async def get_object_info_node(request):
            node_class = request.match_info.get("node_class", None)
            out = {}
            if (node_class is not None) and (node_class in nodes.NODE_CLASS_MAPPINGS):
                out[node_class] = node_info(node_class)
            return web.json_response(out)

        @routes.get("/history")
        async def get_history(request):
            return web.json_response(self.prompt_queue.get_history())

        @routes.get("/history/{prompt_id}")
        async def get_history(request):
            prompt_id = request.match_info.get("prompt_id", None)
            return web.json_response(self.prompt_queue.get_history(prompt_id=prompt_id))

        @routes.get("/queue")
        async def get_queue(request):
            queue_info = {}
            current_queue = self.prompt_queue.get_current_queue()
            queue_info['queue_running'] = current_queue[0]
            queue_info['queue_pending'] = current_queue[1]
            return web.json_response(queue_info)

        @routes.post("/prompt")
        async def post_prompt(request):
            print("got prompt")
            resp_code = 200
            out_string = ""
            json_data =  await request.json()

            print(f"POST Prompt Received: \n {json_data}") # Remove
            if "number" in json_data:
                number = float(json_data['number'])
            else:
                number = self.number
                if "front" in json_data:
                    if json_data['front']:
                        number = -number

                self.number += 1

            if "prompt" in json_data:
                prompt = json_data["prompt"]
                print(f'PROMPT to VALIDATE: {prompt}')
                time.sleep(10)
                valid = execution.validate_prompt(prompt)
                print(f'Prompt for API: {valid}')
                extra_data = {}
                if "extra_data" in json_data:
                    extra_data = json_data["extra_data"]

                if "client_id" in json_data:
                    extra_data["client_id"] = json_data["client_id"]
                    print(f'Client ID: {extra_data["client_id"]}')
                else:
                    extra_data["client_id"] = str(uuid.uuid4().hex)
                    print(f'Client ID: {extra_data["client_id"]}')

                if valid[0]:
                    prompt_id = str(uuid.uuid4())
                    outputs_to_execute = valid[2]
                    self.prompt_id = prompt_id
                    ### User ID added on client side if using API ###
                    user_id = None  
                    channel_id = None
                    server_id = None
                    port = None
                    self.prompt_id = prompt_id
                    user_id = None  
                    channel_id = None
                    print(f'EXTRA DATA: {extra_data}')
                    if "user_id" in extra_data:
                        user_id = extra_data["user_id"]
                    if "channel_id" in extra_data:
                        channel_id = extra_data["channel_id"]
                    if "server_id" in extra_data:
                        server_id = extra_data["server_id"]
                    if "port" in extra_data:
                        port = extra_data["port"]
                    self.user_prompt_map[prompt_id] = {
                            "user_id": user_id,
                            "channel_id": channel_id,
                            "server_id": server_id,
                            "port": port
                        }
                    print(f'USER MAP: {self.user_prompt_map[prompt_id]}')
                    print(f"Added to queue: {number, prompt_id, prompt, extra_data, outputs_to_execute}")
                    ## User ID added on client side if using API ###
                    self.prompt_queue.put((number, prompt_id, prompt, extra_data, outputs_to_execute))
                    print(f"Added to queue: {number, prompt_id, prompt, extra_data, outputs_to_execute}")
                    return web.json_response({"prompt_id": prompt_id, "number": number})
                else:
                    print("invalid prompt:", valid[1])
                    return web.json_response({"error": valid[1], "node_errors": valid[3]}, status=400)
            else:
                return web.json_response({"error": "no prompt", "node_errors": []}, status=400)

        @routes.post("/queue")
        async def post_queue(request):
            json_data =  await request.json()
            if "clear" in json_data:
                if json_data["clear"]:
                    self.prompt_queue.wipe_queue()
            if "delete" in json_data:
                to_delete = json_data['delete']
                for id_to_delete in to_delete:
                    delete_func = lambda a: a[1] == id_to_delete
                    self.prompt_queue.delete_queue_item(delete_func)

            return web.Response(status=200)

        @routes.post("/interrupt")
        async def post_interrupt(request):
            nodes.interrupt_processing()
            return web.Response(status=200)

        @routes.post("/history")
        async def post_history(request):
            json_data =  await request.json()
            if "clear" in json_data:
                if json_data["clear"]:
                    self.prompt_queue.wipe_history()
            if "delete" in json_data:
                to_delete = json_data['delete']
                for id_to_delete in to_delete:
                    self.prompt_queue.delete_history_item(id_to_delete)

            return web.Response(status=200)

        ## Delete Images from Server
        @routes.post("/delete")
        async def post_delete(request):
            data = await request.json()
            print(f'DELETE DATA:{data}')
            if 'filenames' in data:
                self.delete_images(data['filenames'])
            return web.Response(status=200)
        
    ## Shutdown Server
    def shutdown(self, message=None):
        # Check if the pipe exists
        if self.pipe:
            # Send the 'shutdown' command through the pipe
            print("Shutdown Process")
            print(f"Shutdown Message: \nUsing pipe with id {id(self.pipe)} to send shutdown")
            self.delete_all_input_files()
            self.pipe.send('shutdown')
        else:
            print("Cannot shutdown because the pipe is not connected.")
        return 
       
    ## Send Executed Image to API
    def send_message_to_bot(self, message):
        print(f"Function send message to BOT")
        print(f"BOT MESSAGE: {message}")
        # The address of bot's server
        if (self.user_prompt_map[self.prompt_id]["server_id"] is not None):
            server_id = self.user_prompt_map[self.prompt_id]["server_id"]
            port = self.user_prompt_map[self.prompt_id]["port"]
            # Upload Files
            result = self.upload_file(server_id, port, message)
            if result:
                print("Completed Task successfully with Bot Server")
                self.delete_images(message['filenames'])
            else:
                print("Error completing Task with Bot Server")
                self.delete_images(message['filenames'])

    ## Delete Images from Server
    def delete_images(self, filenames: list):
        output_directory = folder_paths.get_output_directory()
        for filename in filenames:
            file_path = os.path.join(output_directory, filename)
            try:
                os.remove(file_path)
                print(f"Deleted file: {file_path}")
            except Exception as e:
                print(f"Could not delete file {file_path}. Reason: {e}")

    ## Delete All Input Files
    def delete_all_input_files(self):
        input_directory = folder_paths.get_input_directory()
        for filename in os.listdir(input_directory):
            file_path = os.path.join(input_directory, filename)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    print(f"Deleted file: {file_path}")
            except Exception as e:
                print(f"Could not delete file {file_path}. Reason: {e}")

    ## Upload files to Bot Server / Task Server - To be converted to a webhook
    def upload_file(self, server_id, port, message):
        filenames = message['filenames']
        # Loop over all filenames and upload each file
        print(f'Filenames: {filenames}')
        output = folder_paths.get_output_directory()
        for filename in filenames:
            filepath = os.path.join(output, filename) #need to use output_dir = folder_paths.get_directory_by_type(type)
            print(f'FILE NAME: {filepath}')
            # Load image
            img = Image.open(filepath)

            print(f'IMAGE OPENED: {img}')

            print(f'IMAGE MODE: {img.mode}')

            if img.mode == 'RGB':
                if img.mode == "RGBA":
                    r, g, b, a = img.split()
                    new_img = Image.merge('RGB', (r, g, b))
                else:
                    new_img = img.convert("RGB")
                print(new_img)
                buffer = BytesIO()
                new_img.save(buffer, format='PNG')
                buffer.seek(0)
                data = buffer.read()
            elif img.mode == 'RGBA':
                if img.mode == "RGBA":
                    _, _, _, a = img.split()
                else:
                    a = Image.new('L', img.size, 255)

                # alpha img
                alpha_img = Image.new('RGBA', img.size)
                alpha_img.putalpha(a)
                alpha_buffer = BytesIO()
                alpha_img.save(alpha_buffer, format='PNG')
                alpha_buffer.seek(0)
                data = alpha_buffer.read()
            else:
                print(f"Unknown image mode for {filename}: {img.mode}")
                continue 

            url = f'{server_id}{port}/upload?filename={filename}' 
            headers = {'Content-Type': 'image/png', "Content-Disposition": f'attachment;filename={filename}'}
            response = requests.post(url, headers=headers, data=data)
            if response.status_code != 200:
                print(f'Failed to upload file {filename}: {response.content}')
            else:
                print(f'Uploaded file {filename}: {response.content}')
            
            response = requests.post(f'{server_id}{port}/executed', json=message)
            if response.status_code != 200:
                print(f'Failed to send message to bot: {response.content}')
                # Add log
            else:
                if response.text == "Bot Done":
                    print(response.text)
                    return True
                else:
                    print(f'Unexpected response from bot: {response.text}')
            
            

    def add_routes(self):
        self.app.add_routes(self.routes)
        self.app.add_routes([
            web.static('/', self.web_root, follow_symlinks=True),
        ])

    def get_queue_info(self):
        prompt_info = {}
        exec_info = {}
        exec_info['queue_remaining'] = self.prompt_queue.get_tasks_remaining()
        prompt_info['exec_info'] = exec_info
        return prompt_info

    async def send(self, event, data, sid=None):
        if isinstance(data, (bytes, bytearray)):
            await self.send_bytes(event, data, sid)
        else:
            await self.send_json(event, data, sid)

    def encode_bytes(self, event, data):
        if not isinstance(event, int):
            raise RuntimeError(f"Binary event types must be integers, got {event}")

        packed = struct.pack(">I", event)
        message = bytearray(packed)
        message.extend(data)
        return message

    async def send_bytes(self, event, data, sid=None):
        message = self.encode_bytes(event, data)

        if sid is None:
            for ws in self.sockets.values():
                await send_socket_catch_exception(ws.send_bytes, message)
        elif sid in self.sockets:
            await send_socket_catch_exception(self.sockets[sid].send_bytes, message)

    async def send_json(self, event, data, sid=None):
        message = {"type": event, "data": data}

        if sid is None:
            for ws in self.sockets.values():
                await send_socket_catch_exception(ws.send_json, message)
        elif sid in self.sockets:
            await send_socket_catch_exception(self.sockets[sid].send_json, message)

    def send_sync(self, event, data, sid=None):
        ## Edit on original send_sync
        ## Error not all events are tracked
        print(f'EVENT: {event}')
        print(f'DATA: {data}')
        # Get the prompt_id
        prompt_id = self.prompt_id
        # Check if the event is 'executed' (i.e., a node has been executed)
        if event == 'executed':
            # Extract the filenames from the data
            filenames = []
            if 'output' in data and 'images' in data['output'] and data['output']['images'][0]['type'] == 'output':
                for image in data['output']['images']:
                    if 'filename' in image:
                        filenames.append(image['filename'])
                # Include the filenames in the data
                data['filenames'] = filenames
                print(f'filenames: {data["filenames"]}')
                print(f'prompt_id: {self.prompt_id}')
                # Send message to bot
                bot_message = {
                    "prompt_id": data['prompt_id'],
                    "user_id": self.user_prompt_map[prompt_id]["user_id"],
                    "channel_id": self.user_prompt_map[prompt_id]["channel_id"],
                    "filenames": data['filenames']
                }
                print(f'BOT MESSAGE: {bot_message}')
                # This could be a POST request or Webhook
                self.send_message_to_bot(bot_message)
        elif event == 'executing':
            if data['node'] is None and data['prompt_id'] == prompt_id:
                print(f'!!!!SHUTDOWN With Data info!!!!')
                self.shutdown()
        ## Edit on Original send_sync

        self.loop.call_soon_threadsafe(
            self.messages.put_nowait, (event, data, sid))

    def queue_updated(self):
        self.send_sync("status", { "status": self.get_queue_info() })

    async def publish_loop(self):
        while True:
            msg = await self.messages.get()
            await self.send(*msg)

    async def start(self, address, port, verbose=True, call_on_start=None):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, address, port)
        await site.start()

        if address == '':
            address = '0.0.0.0'
        if verbose:
            print("Starting server\n")
            print("To see the GUI go to: http://{}:{}".format(address, port))
        if call_on_start is not None:
            print("Auto Start Command Running")
            print(f"Using pipe with id {id(self.pipe)} to send message")
            self.pipe.send('ready')
            call_on_start(address, port)
