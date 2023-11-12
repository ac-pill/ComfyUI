# websock.py
# Functions to control Bot Execution

import requests

def send_message(message, channel_id, user_id, server_id, port):
    bot_message = {
                        "prompt_id": "None",
                        "user_id": user_id,
                        "channel_id": channel_id,
                        "message": message
                    }
    
    response = requests.post(f'{server_id}{port}/status', json=bot_message)
    if response.status_code != 200:
                print(f'Failed to send message to bot: {response.content}')
                # Add log
    else:
        if response.text == "Bot Done":
            return (response.text)
        else:
            return (f'Unexpected response from bot: {response.text}')