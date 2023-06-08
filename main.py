import asyncio
import itertools
import os
import shutil
import threading
import gc
import time
## Adding to main.py
import json
import sys

from concurrent.futures import ThreadPoolExecutor

from comfy.cli_args import init_args # Args set

## Adding to main.py
import comfy.utils

if os.name == "nt":
    import logging
    logging.getLogger("xformers").addFilter(lambda record: 'A matching Triton is not available' not in record.getMessage())

import yaml

import execution
import folder_paths
import server
from server import BinaryEventTypes
from nodes import init_custom_nodes
import comfy.model_management
print("LOADING MAIN.PY - V08")

def prompt_worker(q, server):
    e = execution.PromptExecutor(server)
    while True:
        item, item_id = q.get()
        execution_start_time = time.perf_counter()
        prompt_id = item[1]
        e.execute(item[2], prompt_id, item[3], item[4])
        q.task_done(item_id, e.outputs_ui)
        if server.client_id is not None:
            server.send_sync("executing", { "node": None, "prompt_id": prompt_id }, server.client_id)

        print("Prompt executed in {:.2f} seconds".format(time.perf_counter() - execution_start_time))
        gc.collect()
        comfy.model_management.soft_empty_cache()

async def run(server, address='', port=3000, verbose=True, call_on_start=None):
    await asyncio.gather(server.start(address, port, verbose, call_on_start), server.publish_loop())


def hijack_progress(server):
    def hook(value, total, preview_image_bytes):
        server.send_sync("progress", {"value": value, "max": total}, server.client_id)
        if preview_image_bytes is not None:
            server.send_sync(BinaryEventTypes.PREVIEW_IMAGE, preview_image_bytes, server.client_id)
        print(f'MAIN DATA: VALUE:{value} >> MAX: {total}') ## Print Progress
    comfy.utils.set_progress_bar_global_hook(hook)


def cleanup_temp():
    temp_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "temp")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


def load_extra_path_config(yaml_path):
    with open(yaml_path, 'r') as stream:
        config = yaml.safe_load(stream)
    for c in config:
        conf = config[c]
        if conf is None:
            continue
        base_path = None
        if "base_path" in conf:
            base_path = conf.pop("base_path")
        for x in conf:
            for y in conf[x].split("\n"):
                if len(y) == 0:
                    continue
                full_path = y
                if base_path is not None:
                    full_path = os.path.join(base_path, full_path)
                print("Adding extra search path", x, full_path)
                folder_paths.add_model_folder_path(x, full_path)

def main_func(args_dict, child_conn):

    cleanup_temp()

    print("Starting Server")
    print(f'Args from shared.py:{args_dict}')
    args = init_args(args_dict)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    prompt_server = server.PromptServer(loop)
    q = execution.PromptQueue(server)

    print(f'DONT UPCAST: {args.dont_upcast_attention}')

    # For debugging temp args JSON File
    with open("temp_args.json", 'r') as f:
        this = json.load(f)
    print(f'JSON FILE CONTAINER:{this}')

    if args.dont_upcast_attention:
        print("disabling upcasting of attention")
        os.environ['ATTN_PRECISION'] = "fp16"
    
    init_custom_nodes()
    prompt_server.add_routes()
    hijack_progress(prompt_server)

    if args.cuda_device is not None:
        os.environ['CUDA_VISIBLE_DEVICES'] = str(args.cuda_device)
        print("Set cuda device to:", args.cuda_device)
    
    threading.Thread(target=prompt_worker, daemon=True, args=(q,prompt_server,)).start()

    print(f"Passing child_conn with id {id(child_conn)} from MAIN.PY")

    print("Starting asyncio event loop")

    call_on_start = None
    if args.auto_launch:
        def startup_server(address, port):
            import webbrowser
            webbrowser.open(f"http://{address}:{port}")
        call_on_start = startup_server

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(start_server, args, child_conn, call_on_start)
    if os.name == "nt":
        try:
            loop.run_until_complete(run(server, address=args.listen, port=args.port, verbose=not args.dont_print_server, call_on_start=call_on_start))
        except KeyboardInterrupt:
            pass
    else:
        loop.run_until_complete(run(prompt_server, address=args.listen, port=args.port, verbose=not args.dont_print_server, call_on_start=call_on_start)) # Changing here for clarity
        print("Server is listening on port 3000")

    cleanup_temp()

if __name__ == "__main__":
    main_func(sys.argv[1:])