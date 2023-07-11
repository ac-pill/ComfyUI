import asyncio
import itertools
import os
import shutil
import threading
import gc
import time

from comfy.cli_args import Arguments
import comfy.utils

## Start of Edit Block 1 ##

import json
import sys
from concurrent.futures import ThreadPoolExecutor

## End of Edit Block 1 ##

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

## Start of Edit Block 2 ##
print("LOADING MAIN.PY - V08")

def start_server(child_conn, call_on_start=None):
    global args
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    prompt_server = server.PromptServer(loop, child_conn) # Changing here for adding pipe communication
    q = execution.PromptQueue(prompt_server)

    extra_model_paths_config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "extra_model_paths.yaml")
    if os.path.isfile(extra_model_paths_config_path):
        load_extra_path_config(extra_model_paths_config_path)

    if args.extra_model_paths_config:
        for config_path in itertools.chain(*args.extra_model_paths_config):
            load_extra_path_config(config_path)

    init_custom_nodes()
    prompt_server.add_routes() # Changing name for clarity
    hijack_progress(prompt_server) # Changing name for clarity

    print("Starting prompt_worker thread")
    threading.Thread(target=prompt_worker, daemon=True, args=(q,prompt_server,)).start() # Changing name for clarity

    if args.output_directory:
        output_dir = os.path.abspath(args.output_directory)
        print(f"Setting output directory to: {output_dir}")
        folder_paths.set_output_directory(output_dir)

    if args.quick_test_for_ci:
        exit(0)

    try:
        loop.run_until_complete(run(prompt_server, address=args.listen, port=args.port, verbose=not args.dont_print_server, call_on_start=call_on_start))
    except Exception as e:
        print("Error occurred:", e)
    finally:
        print("MAIN: Complete Task Loop")

## End of Edit Block 2 ##

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

async def run(server, address='', port=8188, verbose=True, call_on_start=None):
    await asyncio.gather(server.start(address, port, verbose, call_on_start), server.publish_loop())


def hijack_progress(server):
    def hook(value, total, preview_image_bytes):
        server.send_sync("progress", {"value": value, "max": total}, server.client_id)
        if preview_image_bytes is not None:
            server.send_sync(BinaryEventTypes.PREVIEW_IMAGE, preview_image_bytes, server.client_id)
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


def main_func(args_dict, child_conn=None, cmdline=False):

    cleanup_temp()

    print("Starting Server")

    args_class = Arguments()

    global args

    ## Check if instance or command line
    if isinstance(args_dict, dict):
        print(f'Args from shared.py:{args_dict}')
        args = args_class.init_args(args_dict)
    else: # assuming list of strings
        print(f'Args from command line:{args_dict}')
        args = args_class.init_args()
        # Note: you'll need to modify init_args function to take command line arguments

    print(f'DONT UPCAST: {args.dont_upcast_attention}')

    # For debugging temp args JSON File
    with open("temp_args.json", 'r') as f:
        this = json.load(f)
    print(f'JSON FILE CONTAINER:{this}')

    if args.dont_upcast_attention:
        print("disabling upcasting of attention")
        os.environ['ATTN_PRECISION'] = "fp16"

    if args.cuda_device is not None:
        os.environ['CUDA_VISIBLE_DEVICES'] = str(args.cuda_device)
        print("Set cuda device to:", args.cuda_device)

    print(f"Passing child_conn with id {id(child_conn)} from MAIN.PY")

    print("Starting asyncio event loop")

    call_on_start = None
    if args.auto_launch:
        def startup_server(address, port):
            import webbrowser
            webbrowser.open(f"http://{address}:{port}")
        call_on_start = startup_server

    if cmdline:
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(start_server, child_conn, call_on_start=call_on_start)
    else:
        start_server(child_conn, call_on_start=call_on_start)

    cleanup_temp()

if __name__ == "__main__":
    main_func(sys.argv[1:], cmdline=True)