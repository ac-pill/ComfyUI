# logger_config.py
import logging

def configure_root_logger():
    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Create a console handler
    console_handler = logging.StreamHandler()
    console_format = logging.Formatter('%(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)')
    console_handler.setFormatter(console_format)

    # Add the console handler to the root logger
    root_logger.addHandler(console_handler)

    # Optionally, you can remove all other handlers that might be added by default or by other modules
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add back your custom console handler
    root_logger.addHandler(console_handler)

# def get_logger(module_name):
#     # Create a custom logger
#     logger = logging.getLogger(module_name)
    
#     # Set the log level
#     logger.setLevel(logging.INFO)
    
#     # Create handlers (console and file handlers)
#     c_handler = logging.StreamHandler()
#     # f_handler = logging.FileHandler('server.log')
    
#     # Create formatters and add them to the handlers
#     c_format = logging.Formatter('%(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)')
#     # f_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)')
#     c_handler.setFormatter(c_format)
#     # f_handler.setFormatter(f_format)
    
#     # Add handlers to the logger
#     logger.addHandler(c_handler)
#     # logger.addHandler(f_handler)
    
#     return logger
