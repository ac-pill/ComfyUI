# logger_config.py
import logging

def get_logger(module_name):
    # Create a custom logger
    logger = logging.getLogger(module_name)
    
    # Set the log level
    logger.setLevel(logging.INFO)
    
    # Create handlers (console and file handlers)
    c_handler = logging.StreamHandler()
    # f_handler = logging.FileHandler('server.log')
    
    # Create formatters and add them to the handlers
    c_format = logging.Formatter('%(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)')
    # f_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)')
    c_handler.setFormatter(c_format)
    # f_handler.setFormatter(f_format)
    
    # Add handlers to the logger
    logger.addHandler(c_handler)
    # logger.addHandler(f_handler)
    
    return logger
