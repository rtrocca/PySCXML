''' 
This file is part of PySCXML.

    PySCXML is free software: you can redistribute it and/or modify
    it under the terms of the GNU Lesser General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    PySCXML is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
    GNU Lesser General Public License for more details.

    You should have received a copy of the GNU Lesser General Public License
    along with PySCXML. If not, see <http://www.gnu.org/licenses/>.
    
    @author: Johan Roxendal
    @contact: johan@roxendal.com
'''

import asyncio
from . import compiler
from .interpreter import Interpreter
from pydispatch import dispatcher
import logging
import os
import errno
import re
from .eventprocessor import Event
from .messaging import get_path
from .datamodel import XPathDatamodel
from lxml import etree
import sys
import time
from scxml.interpreter import CancelEvent

def default_logfunction(label, msg):
    label = label or ""
#    msg = msg or ""
    
    def f(x):
        if etree.iselement(x):
            return etree.tostring(x).strip()
        elif isinstance(x, etree._ElementStringResult):
            return str(x)
        
        return x
    
    if isinstance(msg, list):
        msg = list(map(f, msg))
        try:
            msg = "\n".join(msg)
        except:
            msg = str(msg)
    print("%s%s%s" % (label, ": " if label and msg is not None else "", msg))


class StateMachine:
    '''
    This class provides the entry point for the PySCXML library. 
    '''
    
    def __init__(self, source, log_function=default_logfunction, sessionid=None, default_datamodel="python", setup_session=True):
        '''
        @param source: the scxml document to parse. source may be either:
        
            uri : similar to what you'd write to the open() function. The 
            difference is, StateMachine looks in the PYSCXMLPATH environment variable 
            for documents if none can be found at ".". As such, it's similar to the PYTHONPATH
            environment variable. Set the PYSCXMLPATH variable to exert more fine-grained control
            over the src attribute of <invoke>. self.filename and self.filedir are set as a result.
            
            xml string: if source is an xml string, it's executed as is. 
            self.filedir and self.filename aren't filled. 
            
            file-like object: if source has the .read() method, 
            the result of that method will be executed.
            
        @param log_function: the function to execute on a <log /> element. 
        signature is f(label, msg), where label is a string and msg a string.
        @param sessionid: is stored in the _session variable. Will be automatically
        generated if not provided.
        @param default_datamodel: if omitted, any document started by this instance will have 
        its datamodel expressions evaluated as Python expressions. Set to 'ecmascript' to assume 
        EMCAScript expressions.
        @param setup_session: for internal use.
        @raise IOError 
        @raise xml.parsers.expat.ExpatError 
        '''

        self.is_finished = False
        self.filedir = None
        self.filename = None
        self.compiler = compiler.Compiler()
        self.compiler.default_datamodel = default_datamodel
        self.compiler.log_function = log_function
        
        
        self.sessionid = sessionid or "pyscxml_session_" + str(id(self))
        self.interpreter = Interpreter()
        dispatcher.connect(self.on_exit, "signal_exit", self.interpreter)
        self.logger = logging.getLogger("pyscxml.%s" % self.sessionid)
        self.interpreter.logger = logging.getLogger("pyscxml.%s.interpreter" % self.sessionid)
        self.compiler.logger = logging.getLogger("pyscxml.%s.compiler" % self.sessionid)
        self.doc = self.compiler.parseXML(self._open_document(source), self.interpreter)
        self.interpreter.dm = self.doc.datamodel
        self.datamodel = self.doc.datamodel
        self.doc.datamodel["_x"] = {"self" : self}
        self.doc.datamodel.self = self
        self.doc.datamodel["_sessionid"] = self.sessionid 
        self.doc.datamodel.sessionid = self.sessionid 
        self.name = self.doc.name
        self.is_response = self.compiler.is_response
        if setup_session:
            MultiSession().make_session(self.sessionid, self)
        
    
    def _open_document(self, uri):
        if hasattr(uri, "read"):
            return uri.read()
        elif isinstance(uri, str) and re.search("<(.+:)?scxml", uri): #"<scxml" in uri:
            self.filename = "<string source>"
            self.filedir = None
            return uri
        else:
            path, search_path = get_path(uri, self.filedir or "")
            if path:
                self.filedir, self.filename = os.path.split(os.path.abspath(path))
                return open(path).read()
            else:
                msg = "No such file on the PYSCXMLPATH"
                self.logger.error(msg + ": '%s'" % uri)
                self.logger.error("PYTHONPATH: '%s'" % search_path)
                raise IOError(errno.ENOENT, msg, uri)
    
    async def _start(self):
        self.compiler.instantiate_datamodel()

        await self.interpreter.interpret(self.doc)
    
    async def _start_invoke(self, invokeid=None):
        self.compiler.instantiate_datamodel()
        await self.interpreter.interpret(self.doc, invokeid)
    
    
    async def start(self):
        '''Takes the statemachine to its initial state'''
        if not self.interpreter.running:
            raise RuntimeError("The StateMachine instance may only be started once.")
        else:
            doc = os.path.join(self.filedir, self.filename) if self.filedir else ""
            self.logger.info("Starting %s" % doc)
        self._start()
        await self.interpreter.mainEventLoop()
    
    async def start_threaded(self):
        raise NotImplemented()
        await self._start()
        #eventlet.spawn(self.interpreter.mainEventLoop)
        asyncio.sleep(0)
        
    def isFinished(self):
        '''Returns True if the statemachine has reached it 
        top-level final state or was cancelled.'''
        return self.is_finished
    
    async def cancel(self):
        '''
        Stops the execution of the StateMachine, causing 
        all the states in the current configuration to execute 
        their onexit blocks. The StateMachine instance now no longer
        accepts events. For clarity, consider using the 
        top-level <final /> state in your document instead.  
        '''
        self.interpreter.running = False
        await self.interpreter.externalQueue.put(CancelEvent())
    
    async def send(self, name, data={}):
        '''
        Send an event to the running machine. 
        @param name: the event name (string)
        @param data: the data passed to the _event.data variable (any data type)
        '''
        await self._send(name, data)
        await asyncio.sleep(0)
            
    async def _send(self, name, data={}, invokeid = None, toQueue = None):
        await self.interpreter.send(name, data, invokeid, toQueue)
        
    def In(self, statename):
        '''
        Checks if the state 'statename' is in the current configuration,
        (i.e if the StateMachine instance is currently 'in' that state).
        '''
        return self.interpreter.In(statename)
            
    
    def on_exit(self, sender, final):
        if sender is self.interpreter:
            self.is_finished = True
            for timer in list(self.compiler.timer_mapping.values()):
                # TODO
                #eventlet.greenthread.cancel(timer)
                del timer
            dispatcher.disconnect(self, "signal_exit", self.interpreter)
            dispatcher.send("signal_exit", self, final=final)
    
    
    def __enter__(self):
        self.start_threaded()
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        if not self.isFinished():
            self.cancel()
    

class MultiSession:
    
    def __init__(self, default_scxml_source=None, init_sessions={}, default_datamodel="python", log_function=default_logfunction):
        '''
        MultiSession is a local runtime environment for multiple StateMachine sessions. It's 
        the base class for the PySCXMLServer. You probably won't need to instantiate it directly. 
        @param default_scxml_source: an scxml document source (see StateMachine for the format).
        If one is provided, each call to a sessionid will initialize a new 
        StateMachine instance at that session, running the default document.
        @param init_sessions: the optional keyword arguments run 
        make_session(key, value) on each init_sessions pair, thus initalizing 
        a set of sessions. Set value to None as a shorthand for deferring to the 
        default xml for that session. 
        '''
        self.default_scxml_source = default_scxml_source
        self.sm_mapping = {}
        self.get = self.sm_mapping.get
        self.default_datamodel = default_datamodel
        self.log_function = log_function
        self.logger = logging.getLogger("pyscxml.multisession")
        for sessionid, xml in list(init_sessions.items()):
            self.make_session(sessionid, xml)
            
            
    def __iter__(self):
        return iter(list(self.sm_mapping.values()))
    
    def __delitem__(self, val):
        del self.sm_mapping[val]
    
    def __getitem__(self, val):
        return self.sm_mapping[val]
    
    def __setitem__(self, key, val):
        self.make_session(key, val)
    
    def __contains__(self, item):
        return item in self.sm_mapping
    
    async def start(self):
        ''' launches the initialized sessions by calling start_threaded() on each sm'''
        for sm in self:
            sm.start_threaded()
        await asyncio.sleep(0)
            
    
    def make_session(self, sessionid, source):
        '''initalizes and starts a new StateMachine session at the provided sessionid. 
        
        @param source: A string. if None or empty, the statemachine at this 
        sesssionid will run the document specified as default_scxml_doc 
        in the constructor. Otherwise, the source will be run. 
        @return: the resulting scxml.pyscxml.StateMachine instance. It has 
        not been started, only initialized.
         '''
        assert source or self.default_scxml_source
        if isinstance(source, str):
            sm = StateMachine(source or self.default_scxml_source,
                                sessionid=sessionid,
                                default_datamodel=self.default_datamodel,
                                setup_session=False,
                                log_function=self.log_function)
        else:
            sm = source # source is assumed to be a StateMachine instance
        self.sm_mapping[sessionid] = sm
        #TODO: fix this.
#        if not isinstance(sm.datamodel, XPathDatamodel):
        sm.datamodel.sessions = self
        self.set_processors(sm)
        dispatcher.connect(self.on_sm_exit, "signal_exit", sm)
        return sm
    
    def set_processors(self, sm):
        processors = {"scxml" : {"location" : "#_scxml_" + sm.sessionid}}
        
        if not isinstance(sm.datamodel, XPathDatamodel):
            processors["http://www.w3.org/TR/scxml/#SCXMLEventProcessor"] = {"location" : "#_scxml_" + sm.sessionid}
        
        sm.datamodel["_ioprocessors"] = processors

    
    async def send(self, event, data={}, to_session=None):
        '''send an event to the specified session. if to_session is None or "", 
        the event is sent to all active sessions.'''
        if to_session:
            self[to_session].send(event, data)
        else:
            async for session in self.sm_mapping:
                await self.sm_mapping[session].send(event, data)
    
    def cancel(self):
        for sm in self:
            sm.cancel()
    
    def on_sm_exit(self, sender, final):
        if sender.sessionid in self:
            self.logger.debug("The session '%s' finished" % sender.sessionid)
            del self[sender.sessionid]
        else:
            self.logger.error("The session '%s' reported exit but it " 
            "can't be found in the mapping." % sender.sessionid)
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        self.cancel()
        #asyncio.sleep()

class custom_executable(object):
    '''A decorator for defining custom executable content'''
    def __init__(self, namespace):
        self.namespace = namespace
    
    def __call__(self, f):
        compiler.custom_exec_mapping[self.namespace] = f
        return f

class custom_sendtype(object):
    '''A decorator for defining custom send types'''
    def __init__(self, sendtype):
        self.sendtype = sendtype

    def __call__(self, fun):
        compiler.custom_sendtype_mapping[self.sendtype] = fun
        return fun
    
#class preprocessor(object):
#    '''A decorator for defining replacing xml elements of a 
#    particular namespace with other markup. '''
#    def __init__(self, namespace):
#        self.namespace = namespace
#    
#    def __call__(self, f):
#        compiler.preprocess_mapping[self.namespace] = f
#        return f
    
def register_datamodel(id, klass):
    ''' registers a datamodel class to an id for use with the 
    datamodel attribute of the scxml element.
    Datamodel class must satisfy the interface:
    __setitem__ # modifies 
    __getitem__ # gets
    evalExpr(expr) # returns value
    execExpr(expr) # returns None
    hasLocation(location) # returns bool (check for deep location value)
    isLegalName(name) # returns bool 
    @param klass: A function that returns an instance that satisfies the above api.
    '''
    compiler.datamodel_mapping[id] = klass

    
__all__ = [
    "StateMachine",
    "MultiSession",
    "custom_executable",
    "preprocessor",
    "expr_evaluator",
    "expr_exec",
    "custom_sendtype"
]

if __name__ == "__main__":
    os.environ["PYSCXMLPATH"] = "../../w3c_tests/:../../unittest_xml:../../resources"
    
    logging.basicConfig(level=logging.NOTSET)
    
    if len(sys.argv) > 1:
        sm = StateMachine(sys.argv[1])
        sm.start()
        sys.exit()
    
    '''
    test411.scxml
test413.scxml
test467.scxml
'''
    
    xml = '''
    <scxml xmlns="http://www.w3.org/2005/07/scxml">
    
    <parallel>
        
        <state id="A">
            <onentry>
                <send event="switch" delay="2s"/>
            </onentry>
        </state>
                
        <state id="B">
            <onentry>
                <log expr="'Entered B.'"/>
            </onentry>
            <onexit>
                <log expr="'Exited B.'"/>
            </onexit>
            <state id="B1">
                <onentry>
                    <log expr="'Entered B1.'"/>
                </onentry>
                <transition event="switch" target="B2"/>
            </state>
            <state id="B2">
                <onentry>
                    <log expr="'Enterd B2.'"/>
                </onentry>
                <transition event="switch" target="B1"/>
            </state>
        </state>

    </parallel>
  
</scxml>
    '''
#    dispatcher.connect(default_logfunction, "invoke_log")
#    sm = StateMachine("new_xpath_tests/failed/test152.scxml")
#    sm = StateMachine("xpath_test.xml")
#    sm = StateMachine("assertions_ecmascript/test242.scxml")
#    sm = StateMachine("exit_issue.xml")
    sm = StateMachine(xml)
#    sm = StateMachine("inline_data.xml")
#    os.environ["PYSCXMLPATH"] += ":" + sm.filedir
#    sm = StateMachine("assertions_ecmascript/test154.scxml")
    asyncio.run(sm.start())


