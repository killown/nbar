import logging
import os
import socket
import json
import typing
from typing import (
    Any,
    Callable,
    Type,
    TypeAlias,
    Union,
    Optional,
    List,
    Dict,
    get_origin,
    get_args,
)

from functools import wraps
from gi.repository import GLib  # pyright: ignore

# IMPORT YOUR EXISTING LIBRARY
from pyniri import NiriSocket, NiriError

logger = logging.getLogger(__name__)

# --- Type Validation System (Preserved) ---
TypeInput: TypeAlias = Type | list[Type] | tuple[Type, ...]


def validate_type(
    value: Any,
    expected_type: TypeInput,
    name: str = "value",
    allow_none: bool = False,
    optional: bool = False,
) -> bool:
    """Validate that the provided value matches the expected type."""
    current_type = expected_type
    if optional:
        allow_none = True

    origin = get_origin(current_type)
    args = get_args(current_type)

    if origin is Union and type(None) in args:
        allow_none = True
        inner_types = tuple(t for t in args if t is not type(None))
        current_type = inner_types[0] if len(inner_types) == 1 else Union[inner_types]
        origin = get_origin(current_type)
        args = get_args(current_type)

    if allow_none and value is None:
        return True
    if value is None:
        raise TypeError(f"Invalid {name}: Expected {expected_type}, got None.")

    if current_type is Any:
        return True

    if origin is Union:
        for t in args:
            try:
                if validate_type(value, t, name=name, allow_none=False, optional=False):
                    return True
            except TypeError:
                continue
        type_names = ", ".join(t.__name__ for t in args if hasattr(t, "__name__"))
        raise TypeError(
            f"Invalid {name}: Expected one of ({type_names}), got {type(value).__name__}"
        )

    if origin in (list, tuple):
        if not isinstance(value, origin):
            raise TypeError(
                f"Invalid {name}: Expected {origin.__name__}, got {type(value).__name__}"
            )
        return True

    if not isinstance(current_type, type):
        return True

    if not isinstance(value, current_type):
        raise TypeError(
            f"Invalid {name}: Expected type {current_type.__name__}, got {type(value).__name__}"
        )

    return True


def type_checked(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            annotations = typing.get_type_hints(func)
        except NameError:
            return func(*args, **kwargs)
        for arg_name, arg_value in kwargs.items():
            expected_type = annotations.get(arg_name)
            if expected_type:
                validate_type(arg_value, expected_type, name=arg_name)
        return func(*args, **kwargs)

    return wrapper


def apply_type_checked(cls: type) -> type:
    for name, method in vars(cls).items():
        if isinstance(method, (classmethod, staticmethod)):
            setattr(cls, name, type(method)(type_checked(method.__func__)))
        elif callable(method) and not name.startswith("__"):
            setattr(cls, name, type_checked(method))
    return cls


def handle_ipc_error(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except (socket.error, ConnectionRefusedError, BrokenPipeError, NiriError) as e:
            logger.error(f"Niri IPC error in '{func.__name__}': {e}")
            self.is_connected_flag = False
            return None
        except Exception as e:
            logger.error(f"Unexpected error in '{func.__name__}': {e}")
            return None

    return wrapper


@apply_type_checked
class IPC:
    """
    Wrapper around pyniri.NiriSocket to provide Wayfire-compatible shims for Shellown.
    """

    def __init__(self):
        self.sock: Optional[NiriSocket] = None
        self.event_sock: Optional[socket.socket] = None  # Dedicated for watch()
        self.event_file = None
        self.is_connected_flag = False
        self.socket_path = os.getenv("NIRI_SOCKET")

        self.connect_niri()
        GLib.timeout_add(2000, self.ensure_ipc_connection)

    def connect_niri(self) -> bool:
        """Initializes the NiriSocket from pyniri."""
        try:
            self.sock = NiriSocket()
            self.is_connected_flag = True

            if not self.socket_path and hasattr(self.sock, "socket_path"):
                self.socket_path = self.sock.socket_path

            logger.info("Connected to Niri via pyniri.")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Niri: {e}")
            self.sock = None
            self.is_connected_flag = False
            return False

    def ensure_ipc_connection(self) -> bool:
        if not self.is_connected():
            self.connect_niri()
        return True

    def is_connected(self) -> bool:
        return self.is_connected_flag and self.sock is not None

    @handle_ipc_error
    def close(self) -> None:
        if self.sock:
            self.sock.close()
        if self.event_sock:
            self.event_sock.close()
        self.sock = None
        self.event_sock = None
        self.is_connected_flag = False

    # =========================================================================
    # CORE DELEGATION (Pass through to pyniri)
    # =========================================================================

    @handle_ipc_error
    def perform_action(self, action_name: str, args: Any = None) -> Any:
        return self.sock._action(  # pyright: ignore
            action_name, **(args if isinstance(args, dict) else {})
        )

    @handle_ipc_error
    def perform_request(self, request_name: str) -> Any:
        return self.sock._request(request_name)  # pyright: ignore

    @handle_ipc_error
    def list_outputs(self) -> List[Dict[str, Any]]:
        outputs = self.sock.get_outputs()  # pyright: ignore
        if isinstance(outputs, dict):
            out_list = []
            for k, v in outputs.items():
                if isinstance(v, dict) and "name" not in v:
                    v["name"] = k
                out_list.append(v)
            return out_list
        return outputs or []

    @handle_ipc_error
    def list_views(self) -> List[Dict[str, Any]]:
        return self.sock.get_windows() or []  # pyright: ignore

    @handle_ipc_error
    def list_workspaces(self) -> List[Dict[str, Any]]:
        return self.sock.get_workspaces() or []  # pyright: ignore

    @handle_ipc_error
    def get_version(self) -> str:
        return self.sock.get_version()  # pyright: ignore

    @handle_ipc_error
    def close_view(self, view_id: int) -> bool:
        return self.sock.close_window(view_id)  # pyright: ignore

    @handle_ipc_error
    def set_focus(self, view_id: int) -> bool:
        return self.sock.focus_window(view_id)  # pyright: ignore

    @handle_ipc_error
    def get_workspaces(self) -> list:
        return self.sock.get_workspaces() or []  # pyright: ignore

    @handle_ipc_error
    def set_workspace(self, ws_number: int) -> bool:
        return self.sock.focus_workspace(ws_number)  # pyright: ignore

    @handle_ipc_error
    def run_cmd(self, cmd: str) -> bool:
        """
        Execute a shell command via Niri IPC.
        Using SpawnSh because it takes a String, not a Vec<String>.
        """
        print(cmd)
        return self.sock.spawn_sh(cmd)  # pyright: ignore

    @handle_ipc_error
    def toggle_overview(self) -> bool:
        """
        Toggle the Niri overview mode.
        Maps to Niri's native 'toggle-overview' action via pyniri.
        """
        return self.sock.toggle_overview()  # pyright: ignore

    @handle_ipc_error
    def open_overview(self) -> bool:
        """
        Toggle the Niri overview mode.
        Maps to Niri's native 'open-overview' action via pyniri.
        """
        return self.sock.open_overview()  # pyright: ignore

    @handle_ipc_error
    def close_overview(self) -> bool:
        """
        Toggle the Niri overview mode.
        Maps to Niri's native 'close-overview' action via pyniri.
        """
        return self.sock.close_overview()  # pyright: ignore

    @handle_ipc_error
    def set_option_values(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """Shim: Prevents crash when Verify Niri Plugins tries to enable plugins."""
        return {"status": "success"}

    @handle_ipc_error
    def get_option_value(self, option: str) -> Dict[str, Any]:
        """Shim: Returns mock data for 'core/plugins' check."""
        if option == "core/plugins":
            return {"value": "alpha animate"}
        return {"value": ""}

    @handle_ipc_error
    def connect_client(self, path: str) -> bool:
        """Shim: Niri uses a single socket."""
        return True

    @handle_ipc_error
    def list_input_devices(self) -> List[Dict[str, Any]]:
        """Shim: Returns empty list."""
        return []

    @handle_ipc_error
    def configure_input_device(self, id: int, enabled: bool) -> None:
        """Shim: No-op."""
        pass

    @handle_ipc_error
    def watch(self, events: Optional[List[str]] = None) -> None:
        """
        Start the EventStream on a SEPARATE raw socket connection.
        We cannot reuse self.sock because EventStream blocks the socket.
        """
        if not self.socket_path:
            if self.sock and hasattr(self.sock, "socket_path"):
                self.socket_path = self.sock.socket_path
            else:
                logger.error("Cannot start watch: No Niri socket path found.")
                return

        try:
            if self.event_sock:
                try:
                    self.event_sock.close()
                except Exception:
                    pass

            self.event_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.event_sock.connect(self.socket_path)

            payload = json.dumps("EventStream").encode("utf-8") + b"\n"
            self.event_sock.sendall(payload)

            self.event_file = self.event_sock.makefile(
                "r", encoding="utf-8", buffering=1
            )

            logger.info("Niri EventStream started successfully on dedicated socket.")

        except Exception as e:
            logger.error(f"Failed to start Niri EventStream: {e}")
            if self.event_sock:
                self.event_sock.close()
            self.event_sock = None
            self.event_file = None

    @handle_ipc_error
    def read_next_event(self) -> Optional[Dict[str, Any]]:
        """
        Reads the next line from the dedicated event socket.
        Returns the raw event dict or None if empty/error.
        """
        if not self.event_file:
            return None

        while True:
            try:
                line = self.event_file.readline()

                if not line:
                    return None

                data = json.loads(line)

                if "Ok" in data or "Handled" in data:
                    continue

                return data

            except (socket.error, ValueError, json.JSONDecodeError):
                return None

    @handle_ipc_error
    def get_active_workspace_number(self) -> Optional[int]:
        workspaces = self.list_workspaces() or []
        for ws in workspaces:
            if ws.get("is_active"):
                return ws.get("idx")
        return None

    @handle_ipc_error
    def get_view(self, id: int) -> Optional[Dict[str, Any]]:
        windows = self.list_views() or []
        for w in windows:
            if w.get("id") == id:
                return w
        return None

    @handle_ipc_error
    def get_focused_view(self) -> Optional[Dict[str, Any]]:
        windows = self.list_views() or []
        if not windows:
            return None
        for window in windows:
            if window.get("is_focused"):
                return window

        return windows[0]

    @handle_ipc_error
    def get_workspace_views(self) -> Optional[List[Dict[str, Any]]]:
        focused_ws = self.get_focused_workspace()
        if not focused_ws:
            return []

        focused_workspace_id = focused_ws.get("id")
        return [
            v
            for v in (self.list_views() or [])
            if v.get("workspace_id") == focused_workspace_id
        ]

    @handle_ipc_error
    def get_focused_workspace(self) -> Optional[Dict[str, Any]]:
        workspaces = self.get_workspaces() or []
        for ws in workspaces:
            if ws.get("is_focused") or ws.get("is_active"):
                return ws
        return workspaces[0] if workspaces else None

    @handle_ipc_error
    def get_focused_output(self) -> Optional[Dict[str, Any]]:
        return self.sock.get_focused_output()  # pyright: ignore

    @handle_ipc_error
    def focus_column(self, number: int) -> bool:
        return self.sock.focus_column(number)  # pyright: ignore

    @handle_ipc_error
    def focus_column_right(self) -> bool:
        return self.sock.focus_column_right()  # pyright: ignore

    @handle_ipc_error
    def focus_column_left(self) -> bool:
        return self.sock.focus_column_left()  # pyright: ignore

    @handle_ipc_error
    def focus_column_last(self) -> bool:
        return self.sock.focus_column_last()  # pyright: ignore

    @handle_ipc_error
    def focus_column_right_or_first(self) -> bool:
        return self.sock.focus_column_right_or_first()  # pyright: ignore

    def focus_column_or_monitor_left(self) -> bool:
        return self.sock.focus_column_or_monitor_left()  # pyright: ignore

    @handle_ipc_error
    def list_layers(self) -> List[Dict[str, Any]]:
        return self.sock.get_layers()  # pyright: ignore

    @handle_ipc_error
    def get_keyboard_layouts(self) -> Dict[str, Any]:
        return self.sock.get_keyboard_layouts()  # pyright: ignore

    @handle_ipc_error
    def pick_window(self) -> Optional[Dict[str, Any]]:
        return self.sock.pick_window()  # pyright: ignore

    @handle_ipc_error
    def pick_color(self) -> Optional[Dict[str, float]]:
        return self.sock.pick_color()  # pyright: ignore

    @handle_ipc_error
    def get_overview_state(self) -> Dict[str, bool]:
        return self.sock.get_overview_state()  # pyright: ignore

    @handle_ipc_error
    def quit(self, skip_confirmation: bool = False) -> bool:
        return self.sock.quit(skip_confirmation=skip_confirmation)  # pyright: ignore

    @handle_ipc_error
    def fullscreen_window(self, view_id: Optional[int] = None) -> bool:
        return self.sock.fullscreen_window(id=view_id)  # pyright: ignore

    @handle_ipc_error
    def toggle_window_floating(self, view_id: Optional[int] = None) -> bool:
        return self.sock.toggle_window_floating(id=view_id)  # pyright: ignore

    @handle_ipc_error
    def move_window_to_workspace(
        self, ws_reference: Union[Dict, int, str], view_id: Optional[int] = None
    ) -> bool:
        """Uses NiriSocket's auto-resolution for Index/Name/Id."""
        return self.sock.move_window_to_workspace(  # pyright: ignore
            reference=ws_reference, window_id=view_id
        )

    @handle_ipc_error
    def maximize_column(self, view_id: Optional[int] = None) -> bool:
        return self.sock.maximize_column(id=view_id)  # pyright: ignore

    @handle_ipc_error
    def focus_column_left_or_last(self) -> bool:
        return self.sock.focus_column_left_or_last()  # pyright: ignore

    @handle_ipc_error
    def focus_monitor_right(self) -> bool:
        return self.sock.focus_monitor_right()  # pyright: ignore

    @handle_ipc_error
    def focus_monitor_left(self) -> bool:
        return self.sock.focus_monitor_left()  # pyright: ignore

    @handle_ipc_error
    def center_column(self) -> bool:
        return self.sock.center_column()  # pyright: ignore

    @handle_ipc_error
    def set_column_width(self, change: Dict[str, Any]) -> bool:
        """Expects a dict from SizeChange helper, e.g., {'AdjustFixed': 10}"""
        return self.sock.set_column_width(change=change)  # pyright: ignore

    @handle_ipc_error
    def get_workspace_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        workspaces = self.list_workspaces() or []
        for ws in workspaces:
            if ws.get("name") == name:
                return ws
        return None

    @handle_ipc_error
    def is_overview_active(self) -> bool:
        state = self.get_overview_state()
        if isinstance(state, dict):
            return state.get("is_open", False)
        return False

    @handle_ipc_error
    def move_column_left(self) -> bool:
        return self.sock.move_column_left()  # pyright: ignore

    @handle_ipc_error
    def move_column_right(self) -> bool:
        return self.sock.move_column_right()  # pyright: ignore

    @handle_ipc_error
    def move_column_to_monitor_left(self) -> bool:
        return self.sock.move_column_to_monitor_left()  # pyright: ignore

    @handle_ipc_error
    def move_column_to_monitor_right(self) -> bool:
        return self.sock.move_column_to_monitor_right()  # pyright: ignore

    @handle_ipc_error
    def move_window_down_or_to_workspace_below(self) -> bool:
        return self.sock.move_window_down_or_to_workspace_below()  # pyright: ignore

    @handle_ipc_error
    def move_window_up_or_to_workspace_above(self) -> bool:
        return self.sock.move_window_up_or_to_workspace_above()  # pyright: ignore

    @handle_ipc_error
    def set_column_display(self, display: str) -> bool:
        """Use ColumnDisplay.NORMAL or ColumnDisplay.TABBED."""
        return self.sock.set_column_display(display)  # pyright: ignore

    @handle_ipc_error
    def set_dynamic_cast_window(self, view_id: Optional[int] = None) -> bool:
        return self.sock.set_dynamic_cast_window(id=view_id)  # pyright: ignore

    @handle_ipc_error
    def set_dynamic_cast_monitor(self, output_name: Optional[str] = None) -> bool:
        return self.sock.set_dynamic_cast_monitor(output=output_name)  # pyright: ignore

    @handle_ipc_error
    def clear_dynamic_cast_target(self) -> bool:
        return self.sock.clear_dynamic_cast_target()  # pyright: ignore

    @handle_ipc_error
    def stop_cast(self, session_id: int) -> bool:
        return self.sock.stop_cast(session_id=session_id)  # pyright: ignore

    @handle_ipc_error
    def focus_workspace(self, number: int) -> bool:
        return self.sock.focus_workspace(number)  # pyright: ignore

    @handle_ipc_error
    def focus_workspace_up(self) -> bool:
        return self.sock.focus_workspace_up()  # pyright: ignore

    @handle_ipc_error
    def focus_workspace_down(self) -> bool:
        return self.sock.focus_workspace_down()  # pyright: ignore

    @handle_ipc_error
    def focus_column_first(self) -> bool:
        return self.sock.focus_column_first()  # pyright: ignore

    @handle_ipc_error
    def focus_monitor_up(self) -> bool:
        return self.sock.focus_monitor_up()  # pyright: ignore

    @handle_ipc_error
    def focus_monitor_down(self) -> bool:
        return self.sock.focus_monitor_down()  # pyright: ignore
