import asyncio
from pydispatch import dispatcher
from .node import *
from .datastructures import OrderedSet
from .eventprocessor import Event
from scxml.eventprocessor import ScxmlOriginType
from scxml.datamodel import ECMAScriptDataModel


class Interpreter:
    '''
    The class responsible for keeping track of the execution of the statemachine.
    '''

    def __init__(self):
        self.running = True
        self.exited = False
        self.cancelled = False
        self.configuration = OrderedSet()

        self.internalQueue = asyncio.Queue()
        self.externalQueue = asyncio.Queue()

        self.statesToInvoke = OrderedSet()
        self.historyValue = {}
        self.dm = None
        self.invokeId = None
        self.parentId = None
        self.logger = None

    async def interpret(self, document, invokeId=None):
        '''Initializes the interpreter given an SCXMLDocument instance'''
        self.doc = document
        self.invokeId = invokeId

        transition = Transition(document.rootState)
        transition.target = document.rootState.initial
        transition.exe = document.rootState.initial.exe

        self._executeTransitionContent([transition])
        await self._enterStates([transition])

    async def mainEventLoop(self):
        while self.running:
            enabledTransitions = None
            stable = False

            # now take any newly enabled null transitions and any transitions triggered by internal events
            while self.running and not stable:
                enabledTransitions = self._selectEventlessTransitions()
                if not enabledTransitions:
                    if self.internalQueue.empty():
                        stable = True
                    else:
                        internalEvent = await self.internalQueue.get()  # this call returns immediately if no event is available

                        self.logger.info("internal event found: %s", internalEvent.name)

                        self.dm["__event"] = internalEvent
                        enabledTransitions = self._selectTransitions(internalEvent)

                if enabledTransitions:
                    await self._microstep(enabledTransitions)

            await asyncio.sleep(0)  # Yield control to the event loop

            for state in self.statesToInvoke:
                for inv in state.invoke:
                    await inv.invoke(inv)
            self.statesToInvoke.clear()

            if not self.internalQueue.empty():
                continue

            externalEvent = await self.externalQueue.get()  # this call blocks until an event is available

            if isCancelEvent(externalEvent):
                self.running = False
                continue

            self.logger.info("external event found: %s", externalEvent.name)

            self.dm["__event"] = externalEvent

            for state in self.configuration:
                for inv in state.invoke:
                    if inv.invokeid == externalEvent.invokeid:  # event is the result of an <invoke> in this state
                        self.applyFinalize(inv, externalEvent)
                    if inv.autoforward:
                        await inv.send(externalEvent)

            enabledTransitions = self._selectTransitions(externalEvent)
            if enabledTransitions:
                await self._microstep(enabledTransitions)

        # if we get here, we have reached a top-level final state or some external entity has set running to False        
        await self._exitInterpreter()

    async def _exitInterpreter(self):
        statesToExit = sorted(self.configuration, key=_exitOrder)
        for s in statesToExit:
            for content in s.onexit:
                self._executeContent(content)
            for inv in s.invoke:
                await self._cancelInvoke(inv)
            self.configuration.delete(s)
            if _isFinalState(s) and _isScxmlState(s.parent):
                if self.invokeId and self.parentId and self.parentId in self.dm.sessions:
                    await self.send(["done", "invoke", self.invokeId], s.donedata(), self.invokeId, self.dm.sessions[self.parentId].interpreter.externalQueue)
                self.logger.info("Exiting interpreter")
                dispatcher.send(signal="signal_exit", sender=self, final=s.id)
                self.exited = True
                return
        self.exited = True
        dispatcher.send(signal="signal_exit", sender=self, final=None)

    def _selectEventlessTransitions(self):
        enabledTransitions = OrderedSet()
        atomicStates = list(filter(_isAtomicState, self.configuration))
        atomicStates = sorted(atomicStates, key=_documentOrder)
        for state in atomicStates:
            done = False
            for s in [state] + _getProperAncestors(state, None):
                if done:
                    break
                for t in s.transition:
                    if not t.event and self._conditionMatch(t):
                        enabledTransitions.add(t)
                        done = True
                        break
        filteredTransitions = self._filterPreempted(enabledTransitions)
        return filteredTransitions

    def _selectTransitions(self, event):
        enabledTransitions = OrderedSet()
        atomicStates = list(filter(_isAtomicState, self.configuration))
        atomicStates = sorted(atomicStates, key=_documentOrder)

        for state in atomicStates:
            done = False
            for s in [state] + _getProperAncestors(state, None):
                if done:
                    break
                for t in s.transition:
                    if t.event and _nameMatch(t.event, event.name.split(".")) and self._conditionMatch(t):
                        enabledTransitions.add(t)
                        done = True
                        break

        filteredTransitions = self._filterPreempted(enabledTransitions)
        return filteredTransitions

    def _preemptsTransition(self, t, t2):
        if self._isType1(t):
            return False
        elif self._isType2(t) and self._isType3(t2):
            return True
        elif self._isType3(t):
            return True

        return False

    def _findLCPA(self, states):
        '''
        Gets the least common parallel ancestor of states. 
        Just like findLCA but only for parallel states.
        '''
        for anc in filter(_isParallelState, _getProperAncestors(states[0], None)):
            if all([_isDescendant(s, anc) for s in states[1:]]):
                return anc

    def _isType1(self, t):
        return not t.target

    def _isType2(self, t):
        source = t.source if t.type == "internal" else t.source.parent
        p = self._findLCPA([source] + self.getTargetStates(t.target))
        return p is not None

    def _isType3(self, t):
        return not self._isType2(t) and not self._isType1(t)

    def _filterPreempted(self, enabledTransitions):
        filteredTransitions = []
        for t in enabledTransitions:
            # does any t2 in filteredTransitions preempt t? if not, add t to filteredTransitions
            if not any([self._preemptsTransition(t2, t) for t2 in filteredTransitions]):
                filteredTransitions.append(t)

        return OrderedSet(filteredTransitions)

    async def _microstep(self, enabledTransitions):
        await self._exitStates(enabledTransitions)
        self._executeTransitionContent(enabledTransitions)
        await self._enterStates(enabledTransitions)
        self.logger.info("new config: {" + ", ".join([s.id for s in self.configuration if s.id != "__main__"]) + "}")

    async def _exitStates(self, enabledTransitions):
        statesToExit = OrderedSet()
        for t in enabledTransitions:
            if t.target:
                tstates = self.getTargetStates(t.target)
                if t.type == "internal" and _isCompoundState(t.source) and all([_isDescendant(s, t.source) for s in tstates]):
                    ancestor = t.source
                else:
                    ancestor = self.findLCA([t.source] + tstates)

                for s in self.configuration:
                    if _isDescendant(s, ancestor):
                        statesToExit.add(s)

        for s in statesToExit:
            self.statesToInvoke.delete(s)

        statesToExit.sort(key=_exitOrder)

        for s in statesToExit:
            for h in s.history:
                if h.type == "deep":
                    f = lambda s0: _isAtomicState(s0) and _isDescendant(s0, s)
                else:
                    f = lambda s0: s0.parent == s
                self.historyValue[h.id] = list(filter(f, self.configuration))  # + s.parent 
        for s in statesToExit:
            for content in s.onexit:
                self._executeContent(content)
            for inv in s.invoke:
                await self._cancelInvoke(inv)
            self.configuration.delete(s)

    async def _cancelInvoke(self, inv):
        await inv.cancel()

    def _executeTransitionContent(self, enabledTransitions):
        for t in enabledTransitions:
            self._executeContent(t)
    
    async def _enterStates(self, enabledTransitions):
        statesToEnter = OrderedSet()
        statesForDefaultEntry = OrderedSet()
        for t in enabledTransitions:
            if t.target:
                tstates = self.getTargetStates(t.target)
                if t.type == "internal" and _isCompoundState(t.source) and all([_isDescendant(s,t.source) for s in tstates]):
                    ancestor = t.source
                else:
                    ancestor = self.findLCA([t.source] + tstates)
                for s in tstates:
                    self.addStatesToEnter(s,statesToEnter,statesForDefaultEntry)
                for s in tstates:
                    for anc in _getProperAncestors(s,ancestor):
                        statesToEnter.add(anc)
                        if _isParallelState(anc):
                            for child in _getChildStates(anc):
                                if not any([_isDescendant(s,child) for s in statesToEnter]):
                                    self.addStatesToEnter(child, statesToEnter,statesForDefaultEntry)

        statesToEnter.sort(key=_enterOrder)
        for s in statesToEnter:
            self.statesToInvoke.add(s)
            self.configuration.add(s)
            if self.doc.binding == "late" and s.isFirstEntry:
                s.initDatamodel()
                s.isFirstEntry = False

            for content in s.onentry:
                self._executeContent(content)
            if s in statesForDefaultEntry:
                self._executeContent(s.initial)
            if _isFinalState(s):
                parent = s.parent
                grandparent = parent.parent
                await self.internalQueue.put(Event(["done", "state", parent.id], s.donedata()))
                if _isParallelState(grandparent):
                    if all(map(self.isInFinalState, _getChildStates(grandparent))):
                        await self.internalQueue.put(Event(["done", "state", grandparent.id]))
        for s in self.configuration:
            if _isFinalState(s) and _isScxmlState(s.parent):
                self.running = False
    
    
    def addStatesToEnter(self, state,statesToEnter,statesForDefaultEntry):
        if _isHistoryState(state):
            if state.id in self.historyValue:
                for s in self.historyValue[state.id]:
                    self.addStatesToEnter(s, statesToEnter, statesForDefaultEntry)
                    for anc in _getProperAncestors(s,state):
                        statesToEnter.add(anc)
            else:
                for t in state.transition:
                    for s in self.getTargetStates(t.target):
                        self.addStatesToEnter(s, statesToEnter, statesForDefaultEntry)
        else:
            statesToEnter.add(state)
            if _isCompoundState(state):
                statesForDefaultEntry.add(state)
                for s in self.getTargetStates(state.initial):
                    self.addStatesToEnter(s, statesToEnter, statesForDefaultEntry)
            elif _isParallelState(state):
                for s in _getChildStates(state):
                    self.addStatesToEnter(s,statesToEnter,statesForDefaultEntry)
    
    def isInFinalState(self, s):
        if _isCompoundState(s):
            return any([_isFinalState(s) and s in self.configuration for s in _getChildStates(s)])
        elif _isParallelState(s):
            return all(map(self.isInFinalState, _getChildStates(s)))
        else:
            return False
    
    def findLCA(self, stateList):
        for anc in filter(_isCompoundState, _getProperAncestors(stateList[0], None)):
            if all([_isDescendant(s,anc) for s in stateList[1:]]):
                return anc
    
    
    def applyFinalize(self, inv, event):
        inv.finalize()
    
    def getTargetStates(self, targetIds):
        if targetIds == None:
            pass
        states = []
        for id in targetIds:
            state = self.doc.getState(id)
            if not state:
                raise Exception("The target state '%s' does not exist" % id)
            states.append(state)
        return states
    
    def _executeContent(self, obj):
        if hasattr(obj, "exe") and callable(obj.exe):
            obj.exe()
    
    def _conditionMatch(self, t):
        if not t.cond:
            return True
        else:
            return t.cond()
                
    def In(self, name):
        return name in [x.id for x in self.configuration]
    
    
    async def send(self, name, data=None, invokeid = None, toQueue = None, sendid=None, eventtype="platform", raw=None, language=None):
        """Send an event to the statemachine 
        @param name: a dot delimited string, the event name
        @param data: the data associated with the event
        @param invokeid: if specified, the id of sending invoked process
        @param toQueue: if specified, the target queue on which to add the event
        
        """
        if isinstance(name, str): 
            name = name.split(".")
        if not toQueue: 
            toQueue = self.externalQueue
        evt = Event(name, data, invokeid, sendid=sendid, eventtype=eventtype)
        evt.origin = "#_scxml_" + self.dm.sessionid
        evt.origintype = ScxmlOriginType() if not isinstance(self.dm, ECMAScriptDataModel) else "http://www.w3.org/TR/scxml/#SCXMLEventProcessor"
        evt.raw = raw
        #TODO: and for ecmascript?
        evt.language =  language
        await toQueue.put(evt)
        
            
    async def raiseFunction(self, event, data, sendid=None, type="internal"):
        e = Event(event, data, eventtype=type, sendid=sendid)
        e.origintype = None
        await self.internalQueue.put(e)


def _getProperAncestors(state,root):
#    ancestors = [root] if root else []
    ancestors = []
    while hasattr(state,'parent') and state.parent and state.parent != root:
        state = state.parent
        ancestors.append(state)
    return ancestors
    
    
def _isDescendant(state1,state2):
    while hasattr(state1,'parent'):
        state1 = state1.parent
        if state1 == state2:
            return True
    return False

def _getChildStates(state):
    return state.state + state.final + state.history


def _nameMatch(eventList, event):
    if ["*"] in eventList: return True 
    def prefixList(l1, l2):
        if len(l1) > len(l2): return False 
        for tup in zip(l1, l2):
            if tup[0] != tup[1]:
                return False 
        return True 
    
    for elem in eventList:
        if prefixList(elem, event):
            return True 
    return False 

##
## Various tests for states
##

def _isParallelState(s):
    return isinstance(s,Parallel)


def _isFinalState(s):
    return isinstance(s,Final)


def _isHistoryState(s):
    return isinstance(s,History)


def _isScxmlState(s):
    return s.parent == None


def _isAtomicState(s):
    return isinstance(s, Final) or (isinstance(s,SCXMLNode) and s.state == [] and s.final == [])


def _isCompoundState(s):
    return (isinstance(s,State) and (s.state != [] or s.final != []) ) or s.parent is None #incluce root state


def _enterOrder(s):
    return s.n  

def _exitOrder(s):
    return 0 - s.n

def _documentOrder(s):
    key = [s.n]
    p = s.parent
    while p.n:
        key.append(p.n)
        p = p.parent
    key.reverse()
    return key


class CancelEvent(object):
    pass    

def isCancelEvent(evt):
    return isinstance(evt, CancelEvent)    

