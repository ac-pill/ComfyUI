import argparse
import json
import os
import enum


class EnumAction(argparse.Action):
    """
    Argparse action for handling Enums
    """
    def __init__(self, **kwargs):
        # Pop off the type value
        enum_type = kwargs.pop("type", None)

        # Ensure an Enum subclass is provided
        if enum_type is None:
            raise ValueError("type must be assigned an Enum when using EnumAction")
        if not issubclass(enum_type, enum.Enum):
            raise TypeError("type must be an Enum when using EnumAction")

        # Generate choices from the Enum
        choices = tuple(e.value for e in enum_type)
        kwargs.setdefault("choices", choices)
        kwargs.setdefault("metavar", f"[{','.join(list(choices))}]")

        super(EnumAction, self).__init__(**kwargs)

        self._enum = enum_type

    def __call__(self, parser, namespace, values, option_string=None):
        # Convert value back into an Enum
        value = self._enum(values)
        setattr(namespace, self.dest, value)

class LatentPreviewMethod(enum.Enum):
    NoPreviews = "none"
    Auto = "auto"
    Latent2RGB = "latent2rgb"
    TAESD = "taesd"

# Create Temp Args
JSON_FILE_PATH = "temp_args.json"

args = None  # Initialize args to None

def init_args(arg_dict=None):
    global args
    args = Arguments()
    args.set_args(arg_dict)
    return args

def parse_args(arg_dict=None):
    parser = argparse.ArgumentParser()

    parser.add_argument("--listen", type=str, default="127.0.0.1", metavar="IP", nargs="?", const="0.0.0.0", help="Specify the IP address to listen on (default: 127.0.0.1). If --listen is provided without an argument, it defaults to 0.0.0.0. (listens on all)")
    parser.add_argument("--port", type=int, default=8188, help="Set the listen port.")
    parser.add_argument("--enable-cors-header", type=str, default=None, metavar="ORIGIN", nargs="?", const="*", help="Enable CORS (Cross-Origin Resource Sharing) with optional origin or allow all with default '*'.")
    parser.add_argument("--extra-model-paths-config", type=str, default=None, metavar="PATH", nargs='+', action='append', help="Load one or more extra_model_paths.yaml files.")
    parser.add_argument("--output-directory", type=str, default=None, help="Set the ComfyUI output directory.")
    parser.add_argument("--temp-directory", type=str, default=None, help="Set the ComfyUI temp directory (default is in the ComfyUI directory).")
    parser.add_argument("--auto-launch", action="store_true", help="Automatically launch ComfyUI in the default browser.")
    parser.add_argument("--disable-auto-launch", action="store_true", help="Disable auto launching the browser.")
    parser.add_argument("--cuda-device", type=int, default=None, metavar="DEVICE_ID", help="Set the id of the cuda device this instance will use.")
    parser.add_argument("--dont-upcast-attention", action="store_true", help="Disable upcasting of attention. Can boost speed but increase the chances of black images.")
    cm_group = parser.add_mutually_exclusive_group()
    cm_group.add_argument("--cuda-malloc", action="store_true", help="Enable cudaMallocAsync (enabled by default for torch 2.0 and up).")
    cm_group.add_argument("--disable-cuda-malloc", action="store_true", help="Disable cudaMallocAsync.")
    fp_group = parser.add_mutually_exclusive_group()
    fp_group.add_argument("--force-fp32", action="store_true", help="Force fp32 (If this makes your GPU work better please report it).")
    fp_group.add_argument("--force-fp16", action="store_true", help="Force fp16.")
    fpvae_group = parser.add_mutually_exclusive_group()
    fpvae_group.add_argument("--fp16-vae", action="store_true", help="Run the VAE in fp16, might cause black images.")
    fpvae_group.add_argument("--fp32-vae", action="store_true", help="Run the VAE in full precision fp32.")
    fpvae_group.add_argument("--bf16-vae", action="store_true", help="Run the VAE in bf16, might lower quality.")

    parser.add_argument("--directml", type=int, nargs="?", metavar="DIRECTML_DEVICE", const=-1, help="Use torch-directml.")
    
    parser.add_argument("--disable-ipex-optimize", action="store_true", help="Disables ipex.optimize when loading models with Intel GPUs.")

    parser.add_argument("--preview-method", type=LatentPreviewMethod, default=LatentPreviewMethod.NoPreviews, help="Default preview method for sampler nodes.", action=EnumAction)

    attn_group = parser.add_mutually_exclusive_group()
    attn_group.add_argument("--use-split-cross-attention", action="store_true", help="Use the split cross attention optimization. Ignored when xformers is used.")
    attn_group.add_argument("--use-quad-cross-attention", action="store_true", help="Use the sub-quadratic cross attention optimization . Ignored when xformers is used.")
    attn_group.add_argument("--use-pytorch-cross-attention", action="store_true", help="Use the new pytorch 2.0 cross attention function.")

    parser.add_argument("--disable-xformers", action="store_true", help="Disable xformers.")

    vram_group = parser.add_mutually_exclusive_group()
    vram_group.add_argument("--gpu-only", action="store_true", help="Store and run everything (text encoders/CLIP models, etc... on the GPU).")
    vram_group.add_argument("--highvram", action="store_true", help="By default models will be unloaded to CPU memory after being used. This option keeps them in GPU memory.")
    vram_group.add_argument("--normalvram", action="store_true", help="Used to force normal vram use if lowvram gets automatically enabled.")
    vram_group.add_argument("--lowvram", action="store_true", help="Split the unet in parts to use less vram.")
    vram_group.add_argument("--novram", action="store_true", help="When lowvram isn't enough.")
    vram_group.add_argument("--cpu", action="store_true", help="To use the CPU for everything (slow).")

    parser.add_argument("--disable-smart-memory", action="store_true", help="Force ComfyUI to agressively offload to regular ram instead of keeping models in vram when it can.")


    parser.add_argument("--dont-print-server", action="store_true", help="Don't print server output.")
    parser.add_argument("--quick-test-for-ci", action="store_true", help="Quick test for CI.")
    parser.add_argument("--windows-standalone-build", action="store_true", help="Windows standalone build: Enable convenient things that most people using the standalone windows build will probably enjoy (like auto opening the page on startup).")

    parser.add_argument("--disable-metadata", action="store_true", help="Disable saving prompt metadata in files.")

    if arg_dict is not None:
        # Parse the provided dictionary of arguments
        args = parser.parse_args([], namespace=argparse.Namespace(**arg_dict))
    else:
        # Parse the command-line arguments
        args = parser.parse_args()

    if args.windows_standalone_build:
        args.auto_launch = True

    args_to_json = vars(args)
    args_to_json["preview_method"] = args_to_json["preview_method"].value

    # save args to a json file
    with open(JSON_FILE_PATH, 'w') as f:
        json.dump(args_to_json, f)
        
    return args

class Arguments:
    def __init__(self):
        self._args = parse_args()

    def init_args(self, args_input=None):
        if isinstance(args_input, dict):
            # process dictionary input
            self._args = parse_args(args_input)
        elif isinstance(args_input, list):
            # process list input
            args_dict = {k.lstrip('-'): v for k, v in zip(args_input[::2], args_input[1::2])}
            self._args = parse_args(args_dict)
        elif args_input is None:
            # get default arguments
            self._args = parse_args()
        else:
            # process command line input
            self._args = self.parse_args(vars(argparse.Namespace(**{k: v for k, v in map(lambda x: x.split('='), args_input)})))
        return self._args

    def set_args(self, arg_dict):
        self._args = parse_args(arg_dict)

    def get_args(self):
        if self._args is None:
            # load args from the json file
            try:
                with open(JSON_FILE_PATH, 'r') as f:
                    arg_dict = json.load(f)
                arg_dict["preview_method"] = LatentPreviewMethod(arg_dict["preview_method"])
                self._args = argparse.Namespace(**arg_dict)
            except FileNotFoundError:
                print(f'Error: {JSON_FILE_PATH} not found')
                self._args = parse_args()  # fall back to default args if json file not found
        return self._args

    # Overriding the attribute access methods
    def __getattr__(self, name):
        return getattr(self.get_args(), name)

    def __setattr__(self, name, value):
        if name == "_args":
            super().__setattr__(name, value)
        else:
            setattr(self.get_args(), name, value)

args = Arguments()

