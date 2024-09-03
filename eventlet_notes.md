compiler.py
Queue
urlib2.urlopen
eventlet.greenthread.cancel
eventlet.spawn_after
eventlet.greenpool.GreenPool()

interpreter.py
Queue
eventlet.greenthread.sleep()

invoke.py
eventlet.spawn

messaging.py
eventlet.spawn_n
eventlet.greenthread.sleep

pyscxml_server.py
eventlet.spawn_after
eventlet.spawn
eventlet wsgi
wsgi.server(eventlet.listen)

pyscxml.py
eventlet.spawn
eventlet.greenthread.sleep()
eventlet.greenthread.cancel



pyscxml:
 - compiler
 - messaging

 compiler:
  - messaging
  - invoke
  - patched urllib2

messaging:
 - urllib

invoke:
 - messaging
