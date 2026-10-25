import os
import asyncio
import orjson as json
import time
from concurrent.futures import ThreadPoolExecutor
from src.core.compositor.ipc import IPC
from src.plugins.core._event_loop import get_global_loop
from src.ipc.utils import translate_ipc


class EventServer:
    def __init__(self, logger):
        self.logger = logger
        self.ipcet_paths = [
            self.get_socket_path(),
        ]
        self._cleanup_sockets()

        # Initialize Niri IPC Wrapper
        self.ipc = IPC()

        self.compositor = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.event_queue = asyncio.Queue()
        self.clients = []
        self.event_subscribers = {}
        self.command_handlers = {}
        self.loop = get_global_loop

    def _cleanup_sockets(self) -> None:
        for path in self.ipcet_paths:
            if os.path.exists(path):
                os.remove(path)

    def reconnect_ipc(self) -> bool:
        """
        Re-initializes the Niri IPC connection.
        """
        self.logger.info("Connecting to Niri IPC...")
        try:
            # Assumes IPC.connect_niri() initializes self.ipc.sock (NiriSocket)
            if hasattr(self.ipc, "connect_niri"):
                return self.ipc.connect_niri()
            return self.ipc.sock is not None
        except Exception as e:
            self.logger.error(f"Failed to connect to Niri: {e}")
            return False

    def is_socket_active(self) -> bool:
        """
        Validates the connection state.
        """
        if not self.ipc.is_connected():
            return self.reconnect_ipc()
        return True

    def read_events(self) -> None:
        """
        Blocking thread loop that consumes the pyniri EventStream generator.
        """
        while True:
            if not self.is_socket_active():
                self.logger.warning("IPC disconnected. Retrying in 5 seconds...")
                time.sleep(5)
                continue

            try:
                # NiriSocket.watch() is a blocking generator
                # We consume it here in a dedicated thread
                for event in self.ipc.sock.watch():
                    # Translate Niri raw events to internal panel schema
                    translated_event = translate_ipc(event, self)

                    if not translated_event:
                        continue

                    if self.loop and not self.loop.is_closed():
                        try:
                            # Thread-safe handoff to the Main AsyncIO loop
                            asyncio.run_coroutine_threadsafe(
                                self.event_queue.put(translated_event), self.loop
                            )
                        except RuntimeError:
                            # Loop closed during execution
                            return
                    else:
                        self.logger.warning("Main loop unavailable. Dropping event.")

            except Exception as e:
                self.logger.error(f"Niri EventStream interrupted: {e}")
                time.sleep(1)
                continue

    def register_command(self, command_name: str, handler) -> None:
        if command_name in self.command_handlers:
            self.logger.warning(f"Overwriting IPC command handler for: {command_name}")
        self.command_handlers[command_name] = handler
        self.logger.info(f"IPC command registered: {command_name}")

    def add_event_subscriber(self, event_type, callback) -> None:
        if event_type not in self.event_subscribers:
            self.event_subscribers[event_type] = []
        self.event_subscribers[event_type].append(callback)
        self.logger.info(f"Registered internal subscriber for: {event_type}")

    def handle_msg(self, msg) -> None:
        event_type = msg.get("event")
        self.logger.warning(msg)
        if event_type in self.event_subscribers:
            for callback in self.event_subscribers[event_type]:
                try:
                    asyncio.create_task(callback(msg))
                    self.logger.debug(f"Invoked callback for event: {event_type}")
                except Exception as e:
                    self.logger.error(
                        f"Error executing callback for event '{event_type}': {e}"
                    )
        else:
            self.logger.debug(f"No subscribers for event: {event_type}")

    async def handle_event(self) -> None:
        """
        Async consumer for the event queue. Handles broadcasting to external
        clients and internal plugin subscribers.
        """
        while True:
            event = await self.event_queue.get()
            if not event:
                continue

            # 1. Broadcast to external socket clients (e.g. control scripts)
            serialized_event = json.dumps(event) + b"\n"
            for client in self.clients[:]:
                try:
                    client.write(serialized_event)
                    await client.drain()
                except (ConnectionResetError, BrokenPipeError):
                    self.clients.remove(client)
                    self.logger.warning("Removed disconnected client.")

            # 2. Dispatch to internal plugin subscribers
            event_type = event.get("event")
            if event_type in self.event_subscribers:
                for callback in self.event_subscribers[event_type]:
                    try:
                        # Schedule callback in the current loop
                        asyncio.create_task(callback(event))
                    except Exception as e:
                        self.logger.error(f"Plugin callback error [{event_type}]: {e}")

    async def handle_client(self, reader, writer) -> None:
        """
        Handles incoming external connections to nbar.sock.
        """
        self.clients.append(writer)
        try:
            while True:
                raw_data = await reader.readline()
                if not raw_data:
                    break

                try:
                    message = json.loads(raw_data)
                except Exception:
                    writer.write(
                        json.dumps({"status": "error", "message": "Invalid JSON"})
                        + b"\n"
                    )
                    await writer.drain()
                    continue

                command = message.get("command")
                args = message.get("args", [])

                if command in self.command_handlers:
                    try:
                        response = self.command_handlers[command](args)
                        if isinstance(response, dict):
                            writer.write(json.dumps(response) + b"\n")
                            await writer.drain()
                    except Exception as e:
                        self.logger.error(f"Command execution error: {e}")
                else:
                    writer.write(
                        json.dumps({"status": "error", "message": "Unknown command"})
                        + b"\n"
                    )
                    await writer.drain()

        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            if writer in self.clients:
                self.clients.remove(writer)
            writer.close()
            await writer.wait_closed()

    async def start_server(self, path) -> None:
        server = await asyncio.start_unix_server(
            lambda r, w: self.handle_client(r, w), path=path
        )
        async with server:
            await server.serve_forever()

    async def main(self) -> None:
        """
        Core entry point.
        """
        self.loop = asyncio.get_running_loop()

        # Start blocking Niri event reader in a thread
        self.executor.submit(self.read_events)

        # Start the local Unix server for external tools
        servers = [self.start_server(path) for path in self.ipcet_paths]

        try:
            # Run everything concurrently
            await asyncio.gather(*servers, self.handle_event())
        finally:
            self._cleanup_sockets()

    def get_socket_path(self) -> str:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
        return os.path.join(runtime_dir, "nbar.sock")
