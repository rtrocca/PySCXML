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
from louie import dispatcher
from .messaging import exec_async
from functools import partial
from scxml.messaging import UrlGetter
import logging
import eventlet
from scxml.interpreter import CancelEvent

class InvokeWrapper(object):
    
    def __init__(self):
        self.logger = logging.getLogger("pyscxml.invoke.%s" % type(self).__name__)
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
    
class BaseInvoke(object):
    def __init__(self):
        self.invokeid = None
        self.parentSessionid = None
        self.autoforward = False
        self.src = None
        self.finalize = lambda:None
        
    def start(self, parentQueue):
        pass
    
    def cancel(self):
        pass
         
    def __str__(self):
        return '<Invoke id="%s">' % self.invokeid

class BaseFetchingInvoke(BaseInvoke):
    def __init__(self):
        BaseInvoke.__init__(self)
        self.getter = UrlGetter()
        
        dispatcher.connect(self.onHttpResult, UrlGetter.HTTP_RESULT, self.getter)
        dispatcher.connect(self.onFetchError, UrlGetter.HTTP_ERROR, self.getter)
        dispatcher.connect(self.onFetchError, UrlGetter.URL_ERROR, self.getter)
        
    def onFetchError(self, signal, exception, **named ):
        self.logger.error(str(exception))
        dispatcher.send("error.communication.invoke." + self.invokeid, self, data=exception)

    def onHttpResult(self, signal, result, **named):
        self.logger.debug("onHttpResult " + str(named))
        dispatcher.send("result.invoke.%s" % (self.invokeid), self, data=result)
    

class InvokeSCXML(BaseFetchingInvoke):
    def __init__(self, data):
        BaseFetchingInvoke.__init__(self)
        self.sm = None
        self.parentQueue = None
        self.content = None
        self.initData = data
        self.cancelled = False
        self.default_datamodel = "python"
    
    def start(self, parentId):
        self.parentId = parentId
        if self.src:
            self.getter.get_async(self.src, None)
        else:
            self._start(self.content)
    
    def _start(self, doc):
        if self.cancelled: return
        from scxml.pyscxml import StateMachine
        
        self.sm = StateMachine(doc, 
                               sessionid=self.parentSessionid + "." + self.invokeid, 
                               default_datamodel=self.default_datamodel,
                               log_function=lambda label, val: dispatcher.send(signal="invoke_log", sender=self, label=label, msg=val),
                               setup_session=False)
        self.interpreter = self.sm.interpreter
        self.sm.compiler.initData = self.initData
        self.sm.compiler.parentId = self.parentId
        self.sm.interpreter.parentId = self.parentId
        dispatcher.send("created", sender=self, sm=self.sm)
        self.sm._start_invoke(self.invokeid)
        eventlet.spawn(self.sm.interpreter.mainEventLoop)

    
    def send(self, eventobj):
        if self.sm and not self.sm.isFinished():
            self.sm.interpreter.externalQueue.put(eventobj)
    
    def onHttpResult(self, signal, result, **named):
        self.logger.debug("onHttpResult " + str(named))
        self._start(result)
        
    def cancel(self):
        self.cancelled = True
        if not self.sm: return;
        self.sm.interpreter.cancelled = True
#        self.sm.interpreter.running = False
#        self.sm._send(["cancel", "invoke", self.invokeid], {}, self.invokeid)
        self.sm.interpreter.externalQueue.put(CancelEvent())
    
    

        
class InvokeHTTP(BaseFetchingInvoke):
    def __init__(self):
        BaseFetchingInvoke.__init__(self)
        
    def send(self, eventobj):
        self.getter.get_async(self.content, eventobj.data, type=eventobj.name.join("."))
    
    def start(self, parentQueue):
        dispatcher.send("init.invoke." + self.invokeid, self)
        
    def onHttpResult(self, signal, result, **named):
        self.logger.debug("onHttpResult " + str(named))
        dispatcher.send("result.invoke.%s" % (self.invokeid), self, data={"response" : result})

class InvokeSOAP(BaseInvoke):
    
    def __init__(self):
        BaseInvoke.__init__(self)
        self.client = None
    
    def start(self, parentQueue):
        exec_async(self.init)
    
    def init(self):
        from suds.client import Client #@UnresolvedImport
        self.client = Client(self.content)
        dispatcher.send("init.invoke." + self.invokeid, self)
        
    def send(self, eventobj):
        exec_async(partial(self.soap_send_sync, ".".join(eventobj.name), eventobj.data))
        
    def soap_send_sync(self, method, data):
        result = getattr(self.client.service, method)(**data)
        dispatcher.send("result.invoke.%s.%s" % (self.invokeid, method), self, data=result)

__all__ = ["InvokeWrapper", "InvokeSCXML", "InvokeSOAP", "InvokeHTTP"]