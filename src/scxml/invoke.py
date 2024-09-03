''' 
This file is part of pyscxml.

    pyscxml is free software: you can redistribute it and/or modify
    it under the terms of the GNU Lesser General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    pyscxml is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Lesser General Public License for more details.

    You should have received a copy of the GNU Lesser General Public License
    along with pyscxml.  If not, see <http://www.gnu.org/licenses/>.
    
    @author Johan Roxendal
    @contact: johan@roxendal.com
'''

import asyncio
from pydispatch import dispatcher  # Assuming louie is replaced by pydispatch, which is compatible with Python 3
from .messaging import exec_async  # Replace exec_async function from your updated 'messaging.py'
from functools import partial
from scxml.messaging import UrlGetter
import logging
from scxml.interpreter import CancelEvent

class InvokeWrapper:
    
    def __init__(self):
        self.logger = logging.getLogger(f"pyscxml.invoke.{type(self).__name__}")
        self.invoke = lambda: None
        self.invokeid = None
        self.cancel = lambda: None
        self.invoke_obj = None
        self.autoforward = False
        
    def set_invoke(self, inv):
        inv.logger = self.logger
        self.invoke_obj = inv
        self.invokeid = inv.invokeid
        inv.autoforward = self.autoforward 
        self.cancel = inv.cancel
        self.send = getattr(inv, "send", None)
        
    def finalize(self):
        if self.invoke_obj:
            self.invoke_obj.finalize()
    
class BaseInvoke:
    def __init__(self):
        self.invokeid = None
        self.parentSessionid = None
        self.autoforward = False
        self.src = None
        self.finalize = lambda: None
        
    async def start(self, parentQueue):
        pass
    
    async def cancel(self):
        pass
         
    def __str__(self):
        return f'<Invoke id="{self.invokeid}">'

class BaseFetchingInvoke(BaseInvoke):
    def __init__(self):
        super().__init__()
        self.getter = UrlGetter()
        
        dispatcher.connect(self.on_http_result, UrlGetter.HTTP_RESULT, self.getter)
        dispatcher.connect(self.on_fetch_error, UrlGetter.HTTP_ERROR, self.getter)
        dispatcher.connect(self.on_fetch_error, UrlGetter.URL_ERROR, self.getter)
        
    def on_fetch_error(self, signal, exception, **named):
        self.logger.error(str(exception))
        dispatcher.send(f"error.communication.invoke.{self.invokeid}", self, data=exception)

    def on_http_result(self, signal, result, **named):
        self.logger.debug(f"onHttpResult {named}")
        dispatcher.send(f"result.invoke.{self.invokeid}", self, data=result)
    

class InvokeSCXML(BaseFetchingInvoke):
    def __init__(self, data):
        super().__init__()
        self.sm = None
        self.parentQueue = None
        self.content = None
        self.initData = data
        self.cancelled = False
        self.default_datamodel = "python"
    
    async def start(self, parentId):
        self.parentId = parentId
        if self.src:
            await self.getter.get_async(self.src, None)
        else:
            await self._start(self.content)
    
    async def _start(self, doc):
        if self.cancelled:
            return
        from scxml.pyscxml import StateMachine
        
        self.sm = StateMachine(
            doc, 
            sessionid=f"{self.parentSessionid}.{self.invokeid}", 
            default_datamodel=self.default_datamodel,
            log_function=lambda label, val: dispatcher.send(signal="invoke_log", sender=self, label=label, msg=val),
            setup_session=False
        )
        self.interpreter = self.sm.interpreter
        self.sm.compiler.initData = self.initData
        self.sm.compiler.parentId = self.parentId
        self.sm.interpreter.parentId = self.parentId
        dispatcher.send("created", sender=self, sm=self.sm)
        self.sm._start_invoke(self.invokeid)
        asyncio.create_task(self.sm.interpreter.mainEventLoop())

    async def send(self, eventobj):
        if self.sm and not self.sm.isFinished():
            await self.sm.interpreter.externalQueue.put(eventobj)
    
    def on_http_result(self, signal, result, **named):
        self.logger.debug(f"onHttpResult {named}")
        asyncio.create_task(self._start(result))
        
    async def cancel(self):
        self.cancelled = True
        if not self.sm:
            return
        self.sm.interpreter.cancelled = True
        await self.sm.interpreter.externalQueue.put(CancelEvent())
    
    

class InvokeHTTP(BaseFetchingInvoke):
    def __init__(self):
        super().__init__()
        
    async def send(self, eventobj):
        await self.getter.get_async(self.content, eventobj.data, request_type=eventobj.name.join("."))

    async def start(self, parentQueue):
        dispatcher.send(f"init.invoke.{self.invokeid}", self)
        
    def on_http_result(self, signal, result, **named):
        self.logger.debug(f"onHttpResult {named}")
        dispatcher.send(f"result.invoke.{self.invokeid}", self, data={"response": result})

'''
class InvokeSOAP(BaseInvoke):
    
    def __init__(self):
        super().__init__()
        self.client = None
    
    async def start(self, parentQueue):
        await exec_async(self.init)
    
    async def init(self):
        from suds.client import Client  # Assuming suds is installed and compatible with asyncio
        self.client = Client(self.content)
        dispatcher.send(f"init.invoke.{self.invokeid}", self)
        
    async def send(self, eventobj):
        await exec_async(partial(self.soap_send_sync, ".".join(eventobj.name), eventobj.data))
        
    async def soap_send_sync(self, method, data):
        result = getattr(self.client.service, method)(**data)
        dispatcher.send(f"result.invoke.{self.invokeid}.{method}", self, data=result)
'''

__all__ = ["InvokeWrapper", "InvokeSCXML", "InvokeHTTP"]
