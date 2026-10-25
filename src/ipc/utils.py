def current_compositor(client_instance):
    """
    Identifies the active compositor.
    Explicitly returns 'niri' for the pyniri IPC implementation.
    """
    if client_instance and client_instance.is_connected():
        return "niri"
    return None


def translate_ipc(ev: dict, ipc_server_instance):
    """
    Translates Niri raw events into standardized event names for the panel.
    """
    if not ev:
        return None

    client = ipc_server_instance.ipc
    compositor = current_compositor(client)

    if compositor == "niri":
        ipc_server_instance.compositor = "niri"

        # Identify the event type by the root key
        try:
            etype = next(iter(ev))
            payload = ev[etype]
        except (StopIteration, KeyError, TypeError):
            return None

        # Standardized Mapping
        if etype == "WindowFocusChanged":
            return {"event": "view-focused", "view": {"id": payload.get("id")}}

        if etype == "WindowOpenedOrChanged":
            win = payload.get("window", {})
            return {
                "event": "view-mapped",
                "view": {
                    "id": win.get("id"),
                    "app-id": win.get("app_id"),
                    "title": win.get("title"),
                    "pid": win.get("pid"),
                },
            }

        if etype == "WindowClosed":
            return {"event": "view-unmapped", "view": {"id": payload.get("id")}}

        if etype == "WorkspaceActivated":
            return {
                "event": "workspace-changed",
                "workspace": {"id": payload.get("id")},
            }

        if etype == "KeyboardLayoutsChanged":
            layouts = payload.get("keyboard_layouts", {})
            return {"event": "layout-changed", "index": layouts.get("current_idx", 0)}

        if etype == "OverviewOpenedOrClosed":
            return {"event": "overview-changed", "state": payload.get("is_open", False)}

        # Pass-through for non-standard events
        return {"event": etype, "payload": payload}

    if compositor == "wayfire":
        ipc_server_instance.compositor = "wayfire"
        return ev

    return None
