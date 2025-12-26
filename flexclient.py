import asyncio
import websockets
import socket
import json
from threading import Thread, Lock
import time

# this version of flexclient was updated by kirakira, i do not take any credit for it, and i only fixed some updated APIs and bugs
# magiiiccc
FX_PACKET_MAGIC = 0x88885846

fxsock = None
fxcommlock = Lock()
fx_net_ctrl_ver = 0

def fxReset():

	global fxsock

	if(fxsock != None):
		fxsock.close()
		fxsock = None

def sendFx(msg_content):
	if(fxsock == None):
		return False
	
	msg = FX_PACKET_MAGIC.to_bytes(length=4, byteorder='little') + len(msg_content).to_bytes(length=4, byteorder='little') + msg_content
	
	sent_size = 0
	
	while(sent_size < len(msg)):

		try:

			size = fxsock.send(msg[sent_size:])

		except (TimeoutError, OSError, ConnectionError):

			return False

		if(size == 0):
			return False
		
		sent_size+=size

	return True

def sendFxText(msg_content):
	return sendFx(msg_content.encode('utf-8'))

def sendFxJson(json_rq):
	msg_content = json.dumps(json_rq)
	return sendFxText(msg_content)

def recvFxRaw(data_size):
	if(fxsock == None):
		return None

	msg_data = bytes()

	while(len(msg_data) < data_size):

		try:

			data = fxsock.recv(data_size - len(msg_data))

		except (TimeoutError, OSError, ConnectionError):

			return None

		if(len(data) == 0):
			return None
		
		msg_data+=data

	return msg_data

def recvFx():
	if(fxsock == None):
		return None
	
	base_data = recvFxRaw(8)

	if(base_data == None or int.from_bytes(base_data[:4], byteorder='little') != FX_PACKET_MAGIC):
		return None

	return recvFxRaw(int.from_bytes(base_data[4:8], byteorder='little'))

def recvFxText():
	msg_data = recvFx()
	if(msg_data == None):
		return None
	return msg_data.decode('utf-8')

def recvFxJson():
	msg_data = recvFxText()
	if(msg_data == None):
		return None
	return json.loads(msg_data)

def fxError(web_response, error = "Connection error"):
	web_response["fx_msg_type"] = "fatal_error"
	web_response["error"] = error

def fxHandleError(fx_resp, web_response = None): # returns True if an error was detected and put in the web response

	global fxsock

	if(fx_resp == None or (isinstance(fx_resp, dict) and ("fatal_error" in fx_resp or not "fx_msg_type" in fx_resp))):
		if(web_response != None):
			fxError(web_response, "Connection error" if (fx_resp == None or not isinstance(fx_resp, dict) or not "fatal_error" in fx_resp) else fx_resp["fatal_error"])
		fxReset()
		return True
	
	return False

FX_LOGIN_RQ = "login"
FX_RQ_MAP = {
	"fx_poll" : {
		"response_type": "json"
	},
	"fx_update_module_state" : {
		"response_type": "none"
	},
}

def fxHandleWebRequest(web_msg):

	global fxsock

	msg_type = web_msg["fx_msg_type"]
	web_response = {}

	if((fxsock == None and msg_type != FX_LOGIN_RQ) or (msg_type != FX_LOGIN_RQ and not msg_type in FX_RQ_MAP)):
		fxError(web_response)
		return json.dumps(web_response)

	if(msg_type == FX_LOGIN_RQ):
		
		fxReset()

		time.sleep(1) # wait cuz flexlion might need a bit of time to reset the socket


		fxsock = socket.socket(socket.AF_INET,
						socket.SOCK_STREAM)
		fxsock.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)
		fxsock.settimeout(8)
		try:
			fxsock.connect((web_msg["fx_ip"], int(web_msg["fx_port"])))
			print(f"Connected to target at {web_msg['fx_ip']}:{web_msg['fx_port']}, trying to log in...")
		except:
			fxError(web_response, "Failed to connect to target.")
			print("Failed to connect to target")
			return json.dumps(web_response)
		
		sendFxText(web_msg["fx_pwd"])
		
		fx_resp = recvFxJson()

		if(not fxHandleError(fx_resp, web_response)):

			web_response = fx_resp
			fx_net_ctrl_ver = fx_resp["fx_net_ctrl_ver"]

			print(f"Logged in, build_type = {fx_resp['build_type']}, fx_net_ctrl_ver = {fx_net_ctrl_ver}")

		else:

			print(f"Login failed, error: {web_response['error']}")

		return json.dumps(web_response)
	
	rq_info = FX_RQ_MAP[msg_type]
	resp_type = rq_info["response_type"]
	
	sendFxJson(web_msg)

	if(resp_type == "none"):
		return
	elif(resp_type == "json"):
		fx_resp = recvFxJson()
	elif(resp_type == "plaintext"):
		fx_resp = recvFxText()
	elif(resp_type == "raw"): # does this one send over the websocket?
		fx_resp = recvFx()

	if(not fxHandleError(fx_resp, web_response)):

		web_response = fx_resp

		if(isinstance(web_response, dict)):
			return json.dumps(web_response)
		
		return web_response

	else:
		
		return json.dumps(web_response)

async def webhandler(websocket):

	global fxsock
	global fx_net_ctrl_ver

	while True:
		
		try:
			web_msg = json.loads(await websocket.recv())
		except:
			with fxcommlock:
				fxReset()
			try:
				await websocket.close()
			except:
				pass
			return
		
		with fxcommlock:
			web_response = fxHandleWebRequest(web_msg)

		try:
			if(web_response != None):
				await websocket.send(web_response)
		except:
			with fxcommlock:
				fxReset()
			try:
				await websocket.close()
			except:
				pass
			return
		
		if(fxsock == None):
			try:
				await websocket.close()
			except:
				pass
			return
 
async def main():
    bPort = input("Port(Blank = 8882): ")
    if(bPort == ""):
        bPort = "8882"
    try:
        server = await websockets.serve(webhandler, "localhost", int(bPort))
        print(f"WebSocket server started on localhost:{bPort}")
        await server.wait_closed()
    except OSError as e:
        if e.errno == 48:  # Address already in use
            print(f"Error: Port {bPort} is already in use. Please choose a different port or stop the process using that port.")
        else:
            print(f"Error starting server: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
