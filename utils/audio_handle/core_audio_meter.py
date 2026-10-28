"""只读核心进程的 Windows 播放会话峰值，不访问麦克风、不录制原始声音。"""
import ctypes as c
from ctypes import wintypes as w
import socket
import time
import uuid

class GUID(c.Structure):
    _fields_=[("data",c.c_ubyte*16)]
    def __init__(self,text):
        super().__init__()
        self.data[:]=uuid.UUID(text).bytes_le

def call(obj,slot,restype,argtypes,*args):
    vt=c.cast(obj,c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(restype,c.c_void_p,*argtypes)(vt[slot])(obj,*args)

def checked(hr):
    if hr<0:raise OSError(f"HRESULT 0x{hr&0xffffffff:08x}")

def release(obj):
    if obj and obj.value:call(obj,2,w.ULONG,[])

def query(obj,iid):
    result=c.c_void_p()
    checked(call(obj,0,c.c_long,[c.POINTER(GUID),c.POINTER(c.c_void_p)],c.byref(GUID(iid)),c.byref(result)))
    return result

def listener_pid(port):
    class Row(c.Structure):
        _fields_=[(name,w.DWORD) for name in ("state","local_addr","local_port","remote_addr","remote_port","pid")]
    api=c.WinDLL("iphlpapi").GetExtendedTcpTable
    size=w.ULONG()
    api(None,c.byref(size),False,2,3,0)
    buf=c.create_string_buffer(size.value)
    result=api(buf,c.byref(size),False,2,3,0)
    if result:raise OSError(f"GetExtendedTcpTable={result}")
    count=c.cast(buf,c.POINTER(w.DWORD)).contents.value
    for i in range(count):
        row=Row.from_buffer(buf,4+i*c.sizeof(Row))
        if socket.ntohs(row.local_port&0xffff)==port:return int(row.pid)
    return 0

class CoreSessionMeter:
    def __init__(self,port=8082):
        self.port=port
        self.pid=0
        self.meters=[]
        self.last_scan=0
        self.ole=c.OleDLL("ole32")
        self.ole.CoInitialize(None)
        self.enumerator=c.c_void_p()
        checked(self.ole.CoCreateInstance(c.byref(GUID("bcde0395-e52f-467c-8e3d-c4579291692e")),None,1,
            c.byref(GUID("a95664d2-9614-4f35-a746-de8db63617e6")),c.byref(self.enumerator)))

    def scan(self):
        self.last_scan=time.monotonic()
        for meter in self.meters:release(meter)
        self.meters=[]
        self.pid=listener_pid(self.port)
        if not self.pid:return
        collection=c.c_void_p()
        checked(call(self.enumerator,3,c.c_long,[c.c_int,w.DWORD,c.POINTER(c.c_void_p)],0,1,c.byref(collection)))
        count=w.UINT()
        checked(call(collection,3,c.c_long,[c.POINTER(w.UINT)],c.byref(count)))
        for index in range(count.value):
            device=manager=sessions=c.c_void_p()
            device=c.c_void_p();manager=c.c_void_p();sessions=c.c_void_p()
            try:
                checked(call(collection,4,c.c_long,[w.UINT,c.POINTER(c.c_void_p)],index,c.byref(device)))
                checked(call(device,3,c.c_long,[c.POINTER(GUID),w.DWORD,c.c_void_p,c.POINTER(c.c_void_p)],
                    c.byref(GUID("77aa99a0-1bd6-484f-8bc7-2c654c9a9b6f")),23,None,c.byref(manager)))
                checked(call(manager,5,c.c_long,[c.POINTER(c.c_void_p)],c.byref(sessions)))
                n=c.c_int()
                checked(call(sessions,3,c.c_long,[c.POINTER(c.c_int)],c.byref(n)))
                for j in range(n.value):
                    control=c.c_void_p();control2=c.c_void_p()
                    try:
                        checked(call(sessions,4,c.c_long,[c.c_int,c.POINTER(c.c_void_p)],j,c.byref(control)))
                        control2=query(control,"bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")
                        pid=w.DWORD()
                        checked(call(control2,14,c.c_long,[c.POINTER(w.DWORD)],c.byref(pid)))
                        if pid.value==self.pid:self.meters.append(query(control,"c02216f6-8c67-4b5b-9d00-d008e73e0064"))
                    except OSError:
                        continue
                    finally:
                        release(control2);release(control)
            except OSError:
                pass
            finally:
                release(sessions);release(manager);release(device)
        release(collection)

    def read(self):
        if time.monotonic()-self.last_scan>2:self.scan()
        value=0.0
        for meter in self.meters:
            peak=c.c_float()
            try:
                checked(call(meter,3,c.c_long,[c.POINTER(c.c_float)],c.byref(peak)))
                value=max(value,float(peak.value))
            except OSError:
                self.last_scan=0
        return {"source_pid":self.pid,"session_count":len(self.meters),"peak":round(value,5),"updated_at":time.time()}
