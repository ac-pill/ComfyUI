import asyncio
import itertools
import os
import shutil
import threading
## Adding to main.py
import json
import sys
from multiprocessing import Pipe
parent_conn, child_conn = Pipe()
## Adding to main.py
from comfy.cli_args import args
import comfy.utils

if os.name == "nt":
    import logging
    logging.getLogger("xformers").addFilter(lambda record: 'A matching Triton is not available' not in record.getMessage())

if __name__ == "__main__":
    if args.dont_upcast_attention:
        print("disabling upcasting of attention")
        os.environ['ATTN_PRECISION'] = "fp16"

    if args.cuda_device is not None:
        os.environ['CUDA_VISIBLE_DEVICES'] = str(args.cuda_device)
        print("Set cuda device to:", args.cuda_device)


import yaml

import execution
import folder_paths
import server
from nodes import init_custom_nodes

print("LOADING MAIN.PY - V02")


def prompt_worker(q, server):
    e = execution.PromptExecutor(server)
    while True:
        item, item_id = q.get()
        e.execute(item[2], item[1], item[3], item[4])
        q.task_done(item_id, e.outputs_ui)

async def run(server, address='', port=8188, verbose=True, call_on_start=None):
    await asyncio.gather(server.start(address, port, verbose, call_on_start), server.publish_loop())

def hijack_progress(server):
    def hook(value, total):
        server.send_sync("progress", { "value": value, "max": total}, server.client_id)
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

def main_func(args, is_server_ready, child_conn):
    import argparse

    print("Starting Server")

    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=str, default='')
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument("--dont_print_server", action='store_true')
    parser.add_argument("--auto_launch", action='store_true')
    parser.add_argument("--extra_model_paths_config", nargs="*", action='append', default=[])
    parser.add_argument("--output_directory", type=str, help="Directory for output")
    parser.add_argument("--quick_test_for_ci", action='store_true', help="Quick test for CI")
    # ... add other command-line arguments that your script needs ...
    args = parser.parse_args(args)

    cleanup_temp()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    prompt_server = server.PromptServer(loop, child_conn) # Changing here for adding pipe communication
    q = execution.PromptQueue(server)

    extra_model_paths_config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "extra_model_paths.yaml")
    if os.path.isfile(extra_model_paths_config_path):
        load_extra_path_config(extra_model_paths_config_path)

    if args.extra_model_paths_config:
        for config_path in itertools.chain(*args.extra_model_paths_config):
            load_extra_path_config(config_path)

    init_custom_nodes()
    prompt_server.add_routes() # Changing name for clarity
    hijack_progress(prompt_server) # Changing name for clarity

    threading.Thread(target=prompt_worker, daemon=True, args=(q,prompt_server,)).start() # Changing name for clarity

    if args.output_directory:
        output_dir = os.path.abspath(args.output_directory)
        print(f"Setting output directory to: {output_dir}")
        folder_paths.set_output_directory(output_dir)

    if args.quick_test_for_ci:
        exit(0)

    call_on_start = None
    if args.auto_launch:
        def startup_server(address, port):
            import webbrowser
            webbrowser.open("http://{}:{}".format(address, port))
        call_on_start = startup_server

    if os.name == "nt":
        try:
            loop.run_until_complete(run(prompt_server, address=args.listen, port=args.port, verbose=not args.dont_print_server, call_on_start=call_on_start)) # Changing here for clarity
        except KeyboardInterrupt:
            pass
    else:
        loop.run_until_complete(run(prompt_server, address=args.listen, port=args.port, verbose=not args.dont_print_server, call_on_start=call_on_start)) # Changing here for clarity

    cleanup_temp()

    # After server is ready
    is_server_ready.value = True

if __name__ == "__main__":
    main_func(sys.argv[1:])