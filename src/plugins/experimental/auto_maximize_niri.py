def get_plugin_metadata(_):
    return {
        "id": "org.nbar.plugin.auto_maximize",
        "name": "Niri Auto-Maximize",
        "version": "1.0.0",
        "enabled": True,
        "description": "Automatically maximizes new windows on the focused monitor.",
    }


def get_plugin_class():
    import json
    import os
    import asyncio
    from typing import Set

    from src.plugins.core._base import BasePlugin

    class AutoMaximizePlugin(BasePlugin):
        def __init__(self, panel_instance):
            super().__init__(panel_instance)
            self.maximized_ids: Set[int] = set()
            self._socket_path = os.getenv("NIRI_SOCKET")
            self._stream_task = None

        def on_start(self):
            """Lifecycle hook: Start the async event stream."""
            if not self._socket_path:
                self.logger.error("NIRI_SOCKET not found. Auto-Maximize disabled.")
                return

            self._stream_task = self.run_in_async_task(self.auto_maximize_stream())

        def on_disable(self):
            """Lifecycle hook: Cleanup tasks."""
            if self._stream_task:
                self._stream_task.cancel()
            self.maximized_ids.clear()

        async def auto_maximize_stream(self):
            """
            Async EventStream listener.
            Uses raw unix connection for the stream as per your logic.
            """
            try:
                reader, writer = await asyncio.open_unix_connection(self._socket_path)
                writer.write(b'"EventStream"\n')
                await writer.drain()

                await reader.readline()

                self.logger.info("Niri Auto-Maximize stream active.")

                async for line in reader:
                    raw_json = line.decode().strip()
                    if not raw_json:
                        continue

                    event = json.loads(raw_json)

                    if "WindowOpenedOrChanged" in event:
                        await self._handle_window_event(
                            event["WindowOpenedOrChanged"]["window"]
                        )

                    elif "WindowClosed" in event:
                        self.maximized_ids.discard(event["WindowClosed"]["id"])

            except asyncio.CancelledError:
                self.logger.info("Auto-Maximize stream cancelled.")
            except Exception as e:
                self.logger.error(f"Auto-Maximize Stream Error: {e}")
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except:
                    pass

        async def _handle_window_event(self, win: dict):
            win_id = win["id"]

            if win_id in self.maximized_ids or win.get("is_floating"):
                return

            ws_id = win.get("workspace_id")
            if ws_id is None:
                return

            workspaces = self.ipc.list_workspaces()

            target_ws = next((w for w in workspaces if w["id"] == ws_id), None)
            focused_ws = next((w for w in workspaces if w.get("is_focused")), None)

            if target_ws and focused_ws and target_ws["output"] == focused_ws["output"]:
                outputs_dict = self.ipc.list_outputs()
                out_name = target_ws["output"]
                output_data = outputs_dict.get(out_name)

                if not output_data or not output_data.get("logical"):
                    return

                monitor_width = output_data["logical"]["width"]
                tile_width = win["layout"]["tile_size"][0]

                if tile_width < (monitor_width - 10):
                    self.logger.debug(f"Maximizing new window {win_id} on {out_name}")
                    self.maximized_ids.add(win_id)

                    await asyncio.sleep(0.15)
                    self.ipc.sock.maximize_column(id=win_id)
                else:
                    self.maximized_ids.add(win_id)

    return AutoMaximizePlugin
