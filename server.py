import os
import sys
import asyncio
import traceback

import nodes
import folder_paths
import execution
import uuid
import urllib
import json
import glob
import struct
from PIL import Image, ImageOps
from PIL.PngImagePlugin import PngInfo
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

## Start Block Change Using Requests for Now, replace later with aiohttp
import requests
import time

class NodeProgressTracker:
    def __init__(self, loop, nodes, server_id, port):
        self.loop = loop
        self.nodes = nodes # All nodes list
        self.executed_nodes = []
        self.server_id = server_id
        self.port = port
        self.queue = asyncio.Queue()

    def mark_as_executed(self, node_id):
        """Mark a node as executed."""
        if node_id not in self.executed_nodes:
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
    def get_proc_info(self, last_node_id):
        procinfo = {}
        current = last_node_id
        if current is None:
            procinfo['status'] = 'idle'
            procinfo['current_node'] = None
            procinfo['percentage'] = 100
            procinfo['total'] = self.get_total()
            procinfo['cached'] = self.unprocessed_nodes()
        else:
            procinfo['status'] = 'running'
            procinfo['current_node'] = current
            procinfo['percentage'] = self.get_progress_percentage()
            procinfo['total'] = self.get_total()
            procinfo['cached'] = None
        return procinfo
    
    # Post Status
    def procstat_post(self, last_node_id):
        # asyncio.run_coroutine_threadsafe(self.a_procstat_post(last_node_id), self.loop)
        if last_node_id not in self.executed_nodes:
            self.queue.put_nowait(last_node_id)
            asyncio.run_coroutine_threadsafe(self.handle_queue(), self.loop)

    async def handle_queue(self):
        while not self.queue.empty():
            last_node_id = await self.queue.get()
            await self.a_procstat_post(last_node_id)
            self.queue.task_done()

    async def a_procstat_post(self, last_node_id):
            procinfo = self.get_proc_info(last_node_id)
            server_id = self.server_id
            port = self.port
            print(f'POSTING Progress: {server_id}{port}/status')
            try:
                # Using the aiohttp ClientSession from your existing imports
                async with aiohttp.ClientSession() as session:
                    response = await session.post(f'{server_id}{port}/status', json=procinfo)
                    if response.status == 200:
                        response_text = await response.text()
                        print(response_text)
                    else:
                        print(f"Received a {response.status} status code from the bot.")
            except Exception as e:
                print("Failed to send POST to bot.", traceback.format_exc())
            return web.json_response(procinfo)

## End Block Change

class BinaryEventTypes:
    PREVIEW_IMAGE = 1

async def send_socket_catch_exception(function, message):
    try:
        await function(message)
    except (aiohttp.ClientError, aiohttp.ClientPayloadError, ConnectionResetError) as err:
        print("send error:", err)

class BinaryEventTypes:
    PREVIEW_IMAGE = 1
    UNENCODED_PREVIEW_IMAGE = 2

async def send_socket_catch_exception(function, message):
    try:
        await function(message)
    except (aiohttp.ClientError, aiohttp.ClientPayloadError, ConnectionResetError) as err:
        print("send error:", err)

class BinaryEventTypes:
    PREVIEW_IMAGE = 1
    UNENCODED_PREVIEW_IMAGE = 2

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

        self.supports = ["custom_nodes_from_web"]
        self.prompt_queue = None
        self.loop = loop
        self.messages = asyncio.Queue()
        self.number = 0

        self.args = args ## Get the args from Main

        middlewares = [cache_control]
        if self.args.enable_cors_header:
            middlewares.append(create_cors_middleware(self.args.enable_cors_header))

        self.app = web.Application(client_max_size=104857600, middlewares=middlewares)
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
        self.pipe = pipe ## Hold Server state for Parent Process
        self.msg_prompt = None ## Hold the prompt stated by user
        self.msg_neg_prompt = None ## Hold the negative prompt stated by user
        self.msg_seed = None ## Hold the seed stated by user
        self.node_list = [] ## Hold the total node count
        self.tracker = None ## Hold the tracker class

        self.on_prompt_handlers = []

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
            return web.json_response(list(map(lambda a: os.path.splitext(a)[0], embeddings)))

        @routes.get("/extensions")
        async def get_extensions(request):
            files = glob.glob(os.path.join(
                glob.escape(self.web_root), 'extensions/**/*.js'), recursive=True)
            
            extensions = list(map(lambda f: "/" + os.path.relpath(f, self.web_root).replace("\\", "/"), files))
            
            for name, dir in nodes.EXTENSION_WEB_DIRS.items():
                files = glob.glob(os.path.join(glob.escape(dir), '**/*.js'), recursive=True)
                extensions.extend(list(map(lambda f: "/extensions/" + urllib.parse.quote(
                    name) + "/" + os.path.relpath(f, dir).replace("\\", "/"), files)))

            return web.json_response(extensions)

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
                filepath = os.path.abspath(os.path.join(full_output_folder, filename))

                if os.path.commonpath((upload_dir, filepath)) != upload_dir:
                    return web.Response(status=400)

                if not os.path.exists(full_output_folder):
                    os.makedirs(full_output_folder)

                split = os.path.splitext(filename)

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
                        metadata = PngInfo()
                        if hasattr(original_pil,'text'):
                            for key in original_pil.text:
                                metadata.add_text(key, original_pil.text[key])
                        original_pil = original_pil.convert('RGBA')
                        mask_pil = Image.open(image.file).convert('RGBA')

                        # alpha copy
                        new_alpha = mask_pil.getchannel('A')
                        original_pil.putalpha(new_alpha)
                        original_pil.save(filepath, compress_level=4, pnginfo=metadata)

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
                "system": {
                    "os": os.name,
                    "python_version": sys.version,
                    "embedded_python": os.path.split(os.path.split(sys.executable)[0])[1] == "python_embeded"
                },
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
            info['description'] = obj_class.DESCRIPTION if hasattr(obj_class,'DESCRIPTION') else ''
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
                try:
                    out[x] = node_info(x)
                except Exception as e:
                    print(f"[ERROR] An error occurred while retrieving information for the '{x}' node.", file=sys.stderr)
                    traceback.print_exc()
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
            json_data = self.trigger_on_prompt(json_data)

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
                valid = execution.validate_prompt(prompt)
                print(f'Prompt for API: {valid}')
                extra_data = {}
                if "extra_data" in json_data:
                    extra_data = json_data["extra_data"]

                ## Start Edit
                ## Adding default vars
                user_id = None  
                channel_id = None
                server_id = None
                port = None
                ### User ID added on client side if using API ###
                print(f'EXTRA DATA: {extra_data}')
                if "user_id" in extra_data:
                    user_id = extra_data["user_id"]
                if "channel_id" in extra_data:
                    channel_id = extra_data["channel_id"]
                if "server_id" in extra_data:
                    server_id = extra_data["server_id"]
                if "port" in extra_data:
                    port = extra_data["port"]
                if "prompt" in extra_data:
                    self.msg_prompt = extra_data["prompt"]
                if "neg_prompt" in extra_data:
                    self.msg_neg_prompt = extra_data["neg_prompt"]
                if "seed" in extra_data:
                    self.msg_seed = extra_data["seed"]

                if "client_id" in json_data:
                    extra_data["client_id"] = json_data["client_id"]
                    print(f'Client ID: {extra_data["client_id"]}')
                else:
                    extra_data["client_id"] = str(uuid.uuid4().hex)
                    print(f'Client ID: {extra_data["client_id"]}')
                ### User ID added on client side if using API ###
                if valid[0]:
                    prompt_id = str(uuid.uuid4())
                    self.prompt_id = prompt_id
                    outputs_to_execute = valid[2]
                    ## instantiate tracker
                    self.node_list = list(prompt.keys())
                    print(f'NODE LIST: {self.node_list}')
                    self.tracker = NodeProgressTracker(loop, self.node_list, server_id, port)
                    ## Continue with message assembling
                    self.user_prompt_map[prompt_id] = {
                        "user_id": user_id,
                        "channel_id": channel_id,
                        "server_id": server_id,
                        "port": port
                    }
                    print(f'VALID: {valid}')
                    print(f'USER MAP: {self.user_prompt_map[prompt_id]}')
                    # print(f"Added to queue: {number, prompt_id, prompt, extra_data, outputs_to_execute}")
                    self.prompt_queue.put((number, prompt_id, prompt, extra_data, outputs_to_execute))
                    response = {"prompt_id": prompt_id, "number": number, "node_errors": valid[3]}
                    return web.json_response(response)
                else:
                    print("invalid prompt:", valid[1])
                    ## Edit on Block handling invalid prompts 
                    message = ("invalid prompt:", valid[1])
                    bot_message = {
                        "prompt_id": "None",
                        "user_id": user_id,
                        "channel_id": channel_id,
                        "message": message
                    }
                    print(f'BOT MESSAGE: {bot_message}')
                    #self.send_message_to_bot(bot_message)
                    self.shutdown()
                    ## End Edit Block
                    return web.json_response({"error": valid[1], "node_errors": valid[3]}, status=400)

            else:
                ## Edit on Block handling invalid prompts
                message = ({"error": "no prompt", "node_errors": []})
                bot_message = {
                    "prompt_id": "None",
                    "user_id": user_id,
                    "channel_id": channel_id,
                    "message": message
                }
                print(f'BOT MESSAGE: {bot_message}')
                # self.send_message_to_bot(bot_message)
                self.shutdown()
                ## End Edit Block
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
        
        ## Status Process
        @routes.get("/procstat")
        async def procstat(request):
            return web.json_response(self.tracker.get_proc_info(self.last_node_id))
            

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
                # self.delete_images(message['filenames'])
            else:
                print("Error completing Task with Bot Server")
                # self.delete_images(message['filenames'])

    ## Delete Images from Server
    def delete_images(self, filenames):
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
        print(f'Filenames on UPLOAD: {filenames}')
        output = folder_paths.get_output_directory()
        count = 1
        for filename in filenames:
            filepath = os.path.join(output, filename) 
            print(f'FILE NAME: {filepath}')
            print(f'Count {count}')
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
                new_img = img.convert("RGBA")
                img_buffer = BytesIO()
                new_img.save(img_buffer, format='PNG')
                img_buffer.seek(0)
                data = img_buffer.read()
            else:
                print(f"Unknown image mode for {filename}: {img.mode}")
                continue 

            url = f'{server_id}{port}/upload?filename={filename}' 
            headers = {'Content-Type': 'image/png', "Content-Disposition": f'attachment;filename={filename}'}
            response = requests.post(url, headers=headers, data=data)
            if response.status_code != 200:
                print(f'Failed to upload file {filename}: {response.content}')
                self.delete_images(filename)
            else:
                print(f'Uploaded file {filename}: {response.content}')
                self.delete_images(filename)
            
            # Update Message with current filename
            message['filenames'] = filename
            response = requests.post(f'{server_id}{port}/executed', json=message)
            if response.status_code != 200:
                print(f'Failed to send message to bot: {response.content}')
                # Add log
            else:
                
                if response.text == "Bot Done":
                    print(response.text)
                else:
                    print(f'Unexpected response from bot: {response.text}')
            count += 1
        return True


    def add_routes(self):
        self.app.add_routes(self.routes)

        for name, dir in nodes.EXTENSION_WEB_DIRS.items():
            self.app.add_routes([
                web.static('/extensions/' + urllib.parse.quote(name), dir, follow_symlinks=True),
            ])

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
        if event == BinaryEventTypes.UNENCODED_PREVIEW_IMAGE:
            await self.send_image(data, sid=sid)
        elif isinstance(data, (bytes, bytearray)):
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

    async def send_image(self, image_data, sid=None):
        image_type = image_data[0]
        image = image_data[1]
        max_size = image_data[2]
        if max_size is not None:
            if hasattr(Image, 'Resampling'):
                resampling = Image.Resampling.BILINEAR
            else:
                resampling = Image.ANTIALIAS

            image = ImageOps.contain(image, (max_size, max_size), resampling)
        type_num = 1
        if image_type == "JPEG":
            type_num = 1
        elif image_type == "PNG":
            type_num = 2

        bytesIO = BytesIO()
        header = struct.pack(">I", type_num)
        bytesIO.write(header)
        image.save(bytesIO, format=image_type, quality=95, compress_level=4)
        preview_bytes = bytesIO.getvalue()
        await self.send_bytes(BinaryEventTypes.PREVIEW_IMAGE, preview_bytes, sid=sid)

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
        print(f'LAST NODE: {self.last_node_id}')
        # Get last node and add to executed
        if self.last_node_id is not None:
            self.tracker.mark_as_executed(self.last_node_id)
            print(f"Progress: {self.tracker.get_progress_percentage()}%")
            self.tracker.procstat_post(self.last_node_id)
        # print(f'UNPROCESSED NODE: {self.tracker.unprocessed_nodes()}')
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
                    "filenames": data['filenames'],
                    "prompt": self.msg_prompt,
                    "neg_prompt": self.msg_neg_prompt,
                    "seed": self.msg_seed
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
        runner = web.AppRunner(self.app, access_log=None)
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

    def add_on_prompt_handler(self, handler):
        self.on_prompt_handlers.append(handler)

    def trigger_on_prompt(self, json_data):
        for handler in self.on_prompt_handlers:
            try:
                json_data = handler(json_data)
            except Exception as e:
                print(f"[ERROR] An error occurred during the on_prompt_handler processing")
                traceback.print_exc()

        return json_data
