import aiofiles
import asyncio
from pydispatch import dispatcher

async def main():
    path = './messaging.py'
    async with aiofiles.open(path, mode='r') as file:
            print("Going to read file")
            content = await file.read()
            print("File read")
            dispatcher.send('hello', {'content': content})
if __name__ == '__main__':
    def on_result(signal, **named):
        print(named['sender'].keys())
        print('  result')
    
    dispatcher.connect(on_result, 'hello')
    
    asyncio.run(main())