import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from irc.client import SimpleIRCClient
from dotenv import load_dotenv, find_dotenv

import uvicorn
import os

load_dotenv(find_dotenv())

# Define the IRC server settings
IRC_SERVER = os.getenv("IRC_SERVER", "irc.libera.chat")
IRC_PORT = os.getenv("IRC_PORT", 6667)
IRC_CHANNEL = os.getenv("IRC_CHANNEL", "#testchannel")
IRC_NICK = os.getenv("IRC_NICK", "FastAPIUser")
IRC_USER = os.getenv("IRC_USER", "FastAPIUser")

class IRCtoWebSocketBridge(SimpleIRCClient):
    """Bridge for communication between IRC and WebSockets."""
    def __init__(self, ws_manager, loop):
        super().__init__()
        self.ws_manager = ws_manager
        self.loop = loop

    def on_welcome(self, connection, event):
        """Called upon successful connection to the IRC server."""
        print(f"Connected to IRC server. Joining {IRC_CHANNEL}...")
        connection.join(IRC_CHANNEL)

    def on_pubmsg(self, connection, event):
        """Handles public messages from the IRC channel."""
        # This is a key step: we receive an IRC message and broadcast it to our WebSocket clients.
        nick = event.source.nick
        message = event.arguments[0]
        full_message = f"{nick}: {message}"
        # Schedule the message broadcast on the FastAPI event loop
        self.loop.create_task(self.ws_manager.broadcast(full_message))

    def on_disconnect(self, connection, event):
        """Handles disconnection from the IRC server."""
        print("Disconnected from IRC server.")

class ConnectionManager:
    """Manages WebSocket connections and broadcasts messages."""
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

app = FastAPI()
manager = ConnectionManager()

@app.on_event("startup")
async def startup_event():
    """Initializes the IRC bridge and client on FastAPI startup."""
    loop = asyncio.get_event_loop()
    irc_bridge = IRCtoWebSocketBridge(manager, loop)
    # The IRC client runs in a separate thread to not block the main event loop.
    def run_irc_client():
        try:
            irc_bridge.connect(IRC_SERVER, IRC_PORT, IRC_NICK, username=IRC_USER)
            irc_bridge.start()
        except Exception as e:
            print(f"IRC client error: {e}")
    
    # Start the IRC client in the background.
    loop.run_in_executor(None, run_irc_client)
    app.state.irc_bridge = irc_bridge

@app.on_event("shutdown")
def shutdown_event():
    """Disconnects the IRC client when FastAPI shuts down."""
    if hasattr(app.state, 'irc_bridge'):
        app.state.irc_bridge.disconnect()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle messages from the web client, send to IRC
            app.state.irc_bridge.connection.privmsg(IRC_CHANNEL, data)
            # You can also broadcast the message to other web clients directly
            # to provide immediate feedback.
            await manager.broadcast(f"Web Client: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print("Web Client disconnected")

# For testing, you can serve a simple HTML page to interact with the WebSocket
@app.get("/")
async def get():
    return """
    <!DOCTYPE html>
    <html>
        <head>
            <title>FastAPI IRC Chat</title>
        </head>
        <body>
            <h1>FastAPI IRC Chat</h1>
            <input type="text" id="messageText" autocomplete="off"/>
            <button onclick="sendMessage()">Send</button>
            <ul id="messages"></ul>
            <script>
                var ws = new WebSocket("ws://localhost:8000/ws");
                ws.onmessage = function(event) {
                    var messages = document.getElementById('messages');
                    var message = document.createElement('li');
                    var content = document.createTextNode(event.data);
                    message.appendChild(content);
                    messages.appendChild(message);
                };
                function sendMessage() {
                    var input = document.getElementById("messageText");
                    ws.send(input.value);
                    input.value = '';
                }
            </script>
        </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
