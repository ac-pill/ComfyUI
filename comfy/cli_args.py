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
    parser.add_argument("--auto-launch", action="store_true", help="Automatically launch ComfyUI in the default browser.")
    parser.add_argument("--cuda-device", type=int, default=None, metavar="DEVICE_ID", help="Set the id of the cuda device this instance will use.")
    parser.add_argument("--dont-upcast-attention", action="store_true", help="Disable upcasting of attention. Can boost speed but increase the chances of black images.")
    parser.add_argument("--force-fp32", action="store_true", help="Force fp32 (If this makes your GPU work better please report it).")
    parser.add_argument("--directml", type=int, nargs="?", metavar="DIRECTML_DEVICE", const=-1, help="Use torch-directml.")

    parser.add_argument("--disable-previews", action="store_true", help="Disable showing node previews.")
    parser.add_argument("--default-preview-method", type=str, default=LatentPreviewMethod.Auto, metavar="PREVIEW_METHOD", help="Default preview method for sampler nodes.")

    attn_group = parser.add_mutually_exclusive_group()
    attn_group.add_argument("--use-split-cross-attention", action="store_true", help="Use the split cross attention optimization instead of the sub-quadratic one. Ignored when xformers is used.")
    attn_group.add_argument("--use-pytorch-cross-attention", action="store_true", help="Use the new pytorch 2.0 cross attention function.")

    parser.add_argument("--disable-xformers", action="store_true", help="Disable xformers.")

    vram_group = parser.add_mutually_exclusive_group()
    vram_group.add_argument("--highvram", action="store_true", help="By default models will be unloaded to CPU memory after being used. This option keeps them in GPU memory.")
    vram_group.add_argument("--normalvram", action="store_true", help="Used to force normal vram use if lowvram gets automatically enabled.")
    vram_group.add_argument("--lowvram", action="store_true", help="Split the unet in parts to use less vram.")
    vram_group.add_argument("--novram", action="store_true", help="When lowvram isn't enough.")
    vram_group.add_argument("--cpu", action="store_true", help="To use the CPU for everything (slow).")

    parser.add_argument("--dont-print-server", action="store_true", help="Don't print server output.")
    parser.add_argument("--quick-test-for-ci", action="store_true", help="Quick test for CI.")
    parser.add_argument("--windows-standalone-build", action="store_true", help="Windows standalone build: Enable convenient things that most people using the standalone windows build will probably enjoy (like auto opening the page on startup).")

    if arg_dict is not None:
        # Parse the provided dictionary of arguments
        args = parser.parse_args([], namespace=argparse.Namespace(**arg_dict))
    else:
        # Parse the command-line arguments
        args = parser.parse_args()

    if args.windows_standalone_build:
        args.auto_launch = True

    # save args to a json file
    with open(JSON_FILE_PATH, 'w') as f:
        json.dump(vars(args), f)
        
    return args

class Arguments:
    def __init__(self):
        self._args = parse_args()

    def set_args(self, arg_dict):
        self._args = parse_args(arg_dict)

    def get_args(self):
        if self._args is None:
            # load args from the json file
            try:
                with open(JSON_FILE_PATH, 'r') as f:
                    arg_dict = json.load(f)
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
