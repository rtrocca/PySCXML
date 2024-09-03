'''
Updated version to Python 3 using asyncio and aiohttp.

Author: Johan
Updated: [Your Name], [Date]
'''

import asyncio
from aiohttp import ClientSession, ClientResponseError, TCPConnector
from urllib.parse import urlencode, urlparse
from functools import partial
from pydispatch import dispatcher  # Assuming louie is replaced by pydispatch, which is compatible with Python 3
import os
import aiofiles
#import logging
import ssl
import certifi


#logger = logging.Logger(__name__)
class UrlGetter:
    HTTP_RESULT = "HTTP_RESULT"
    HTTP_ERROR = "HTTP_ERROR"
    URL_ERROR = "URL_ERROR"

    async def get_async(self, url, data, request_type=None, content_type="application/x-www-form-urlencoded"):
        # Schedules the coroutine to run in the background
        #asyncio.create_task(self.get_sync(url, data, request_type=request_type, content_type=content_type))
        await self.get_sync(url, data, request_type=request_type, content_type=content_type)

    async def get_sync(self, url, data, request_type=None, content_type="application/x-www-form-urlencoded"):
        print('get_sync', url)
        # Check if the URL uses the file protocol
        parsed_url = urlparse(url)
        if parsed_url.scheme == 'file':
            print('Handle file protocol')
            await self.handle_file_protocol(parsed_url.path, url)
            print('After handle file protocol')
            return

        # Preparing data and headers
        if isinstance(data, dict):
            data = urlencode(data)
        headers = {"Content-Type": content_type}
        method = request_type.upper() if request_type else "POST"

        ssl_context = ssl.create_default_context(cafile=certifi.where())
        conn = TCPConnector(ssl=ssl_context)
        async with ClientSession(connector=conn) as session:
            try:
                if method == "GET":
                    async with session.get(url, headers=headers) as response:
                        await self.handle_response(response, url)
                elif method == "POST":
                    async with session.post(url, data=data, headers=headers) as response:
                        await self.handle_response(response, url)
                else:
                    # Assuming 'type' can be other RESTful methods
                    async with session.request(method, url, data=data, headers=headers) as response:
                        await self.handle_response(response, url)
            except ClientResponseError as e:
                dispatcher.send(self.HTTP_ERROR, self, exception=e)
            except (Exception, ValueError) as e:
                dispatcher.send(self.URL_ERROR, self, exception=e, url=url)

    async def handle_response(self, response, url):
        # Process the response from the server
        if response.status == 200:
            result = await response.text()
            dispatcher.send(self.HTTP_RESULT, self, result=result, source=url, code=response.status)
        else:
            e = ClientResponseError(
                response.request_info, response.history,
                code=response.status, message=f"A code {response.status} HTTP error occurred when trying to send to target {url}"
            )
            dispatcher.send(self.HTTP_ERROR, self, exception=e)

    async def handle_file_protocol(self, path, url):
        # Handle the file protocol by reading directly from the filesystem
        try:
            print('Opening', path, f"({url})")
            async with aiofiles.open(path, mode='r') as file:
                print("Going to read file")
                content = await file.read()
                print("File read")
                dispatcher.send(self.HTTP_RESULT, self, result=content, source=url, code=200)
        except FileNotFoundError as e:
            print("File not found")
            dispatcher.send(self.URL_ERROR, self, exception=e, url=url)
        except Exception as e:
            print("Exception", e)
            dispatcher.send(self.HTTP_ERROR, self, exception=e)


def get_path(local_path, additional_paths=""):
    prefix = additional_paths + ":" if additional_paths else ""
    search_path = (prefix + os.getcwd() + ":" + os.environ.get("PYSCXMLPATH", "").strip(":")).split(":")
    paths = [os.path.join(folder, local_path) for folder in search_path]
    for path in paths:
        if os.path.isfile(path):
            return (path, search_path)
    return (None, search_path)


if __name__ == '__main__':
    getter = UrlGetter()

    def on_http_result(signal, **named):
        print('  result', named)

    def on_http_error(signal, **named):
        print('  error', named["exception"])
        raise named["exception"]

    def on_url_error(signal, **named):
        print('  error', named)

    # Connect signals to the handler functions
    dispatcher.connect(on_http_result, UrlGetter.HTTP_RESULT, getter)
    dispatcher.connect(on_http_error, UrlGetter.HTTP_ERROR, getter)
    dispatcher.connect(on_url_error, UrlGetter.URL_ERROR, getter)

    # Example call
    #asyncio.run(getter.get_async("https://www.gutenberg.org/ebooks/68283.txt.utf-8", {}), debug=True)
    #asyncio.run(getter.get_async("file:messaging.py", {}), debug=True)

    #asyncio.run(getter.handle_file_protocol('messaging.py', 'file:messaging.py'), debug=True)

    asyncio.run(getter.get_async("http://127.0.0.1:8000/messaging.py", {}, request_type='GET'), debug=True)