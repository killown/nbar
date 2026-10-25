import threading
import collections
import json
from gi.repository import GLib


def get_plugin_metadata(_):
    return {
        "id": "org.nbar.plugin.event_manager",
        "name": "Event Manager",
        "version": "2.2.0-DEBUG",
        "enabled": True,
        "description": "Core background service with extreme diagnostic logging.",
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin

    class EventManagerPlugin(BasePlugin):
        def __init__(self, panel_instance):
            super().__init__(panel_instance)
            self._stop_event = threading.Event()
            self._watch_thread = None
            self.event_subscribers = collections.defaultdict(list)
            self._known_window_ids = set()

            self._event_map = {
                "WindowFocusChanged": "view-focused",
                "WindowOpenedOrChanged": "view-mapped",
                "WindowClosed": "view-unmapped",
                "WorkspaceActivated": "workspace-changed",
                "KeyboardLayoutsChanged": "layout-changed",
                "OverviewOpenedOrClosed": "overview-changed",
            }

        def on_start(self):
            self._stop_event.clear()
            self._watch_thread = threading.Thread(
                target=self._watch_niri_events, name="NiriEventWatcher", daemon=True
            )
            self._watch_thread.start()
            self.logger.info("Niri Event Watcher thread started.")

        def on_stop(self):
            self._stop_event.set()

        def _watch_niri_events(self):
            if not self.ipc.ensure_ipc_connection():
                self.logger.error("IPC not connected. Watcher aborted.")
                return

            try:
                for event in self.ipc.sock.watch():
                    if self._stop_event.is_set():
                        break
                    # Only serialize the event when DEBUG is actually enabled;
                    # the f-string used to run json.dumps on every single event.
                    if self.logger.isEnabledFor(10):
                        self.logger.debug(f"[RAW NIRI] {json.dumps(event)}")
                    self._dispatch_niri_event(event)
            except Exception as e:
                self.logger.error(f"Event watcher crashed: {e}", exc_info=True)

        def _dispatch_niri_event(self, niri_event: dict):
            if not niri_event:
                return

            event_type = next(iter(niri_event))
            payload = niri_event[event_type]
            internal_type = self._event_map.get(event_type, event_type)

            # Overview events are renamed below; everything else keeps the
            # mapped name. Bail out early when nobody listens so the GTK main
            # loop is not woken up for the many events nobody consumes.
            if (
                event_type not in ("OverviewActivated", "OverviewDeactivated")
                and not self.event_subscribers.get(internal_type)
            ):
                return

            # 1. Identify if it's an Overview event (Niri native)
            if event_type == "OverviewActivated":
                internal_type = "overview-changed"
                state = True
            elif event_type == "OverviewDeactivated":
                internal_type = "overview-changed"
                state = False
            else:
                state = None

            # 2. Extract Window ID (only exists for Window-specific events)
            is_dict = isinstance(payload, dict)
            win_data = payload.get("window", payload) if is_dict else {}
            wid = (
                win_data.get("id")
                if isinstance(win_data, dict)
                else (payload if not is_dict else None)
            )

            # 3. Construct the message
            msg = {
                "event": internal_type,
                "type": internal_type,
                "niri_raw": event_type,
                "payload": payload,
                "state": state,  # This carries the True/False for Overview plugins
            }

            workspace_id = win_data.get("workspace_id")

            # 4. The "View" Shim (The part that was hiding your events)
            # We add a fallback so the 'view' key exists even if empty, or skip the check
            if wid is not None:
                msg["view"] = {
                    "id": wid,
                    "app-id": win_data.get("app_id", "unknown")
                    if isinstance(win_data, dict)
                    else "unknown",
                    "title": win_data.get("title", "")
                    if isinstance(win_data, dict)
                    else "",
                    "pid": win_data.get("pid", 0) if isinstance(win_data, dict) else 0,
                    "role": "toplevel",
                    "type": "toplevel",
                    "output-id": workspace_id,
                    "workspace": workspace_id,
                }
            else:
                msg["view"] = {"id": None, "type": "none", "role": "none"}

            GLib.idle_add(self._notify_subscribers, internal_type, msg)

        def _notify_subscribers(self, event_type, msg):
            subs = self.event_subscribers.get(event_type)
            if not subs:
                return False

            for callback, plugin_name in subs:
                try:
                    # Logic Check: If the plugin uses a decorator that expects msg['type']
                    # and it's missing, it crashes here.
                    callback(msg)
                except Exception as e:
                    self.logger.error(
                        f"CRASH in subscriber '{plugin_name}' for event '{event_type}': {e}\n"
                        f"Message content: {msg}",
                        exc_info=True,
                    )
            return False

        def subscribe_to_event(self, event_type, callback, plugin_name=None) -> None:
            self.event_subscribers[event_type].append((callback, plugin_name))
            self.logger.debug(f"New sub: {plugin_name} -> {event_type}")

        def unsubscribe_from_event(self, event_type, callback) -> None:
            if event_type in self.event_subscribers:
                self.event_subscribers[event_type] = [
                    s for s in self.event_subscribers[event_type] if s[0] != callback
                ]

    return EventManagerPlugin
