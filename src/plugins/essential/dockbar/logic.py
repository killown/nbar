"""Logic engine for the Dockbar plugin for Niri."""


class DockLogic:
    def __init__(self, plugin):
        self.p = plugin

    def is_overview_enabled(self):
        """Reports whether the niri overview is available."""
        # Overview is a core feature in Niri, always assume True
        # unless checking specific state via IPC.
        return True

    def get_orientation(self):
        """Access metadata to determine container-based orientation."""
        from .dockbar import get_plugin_metadata

        meta = get_plugin_metadata(self.p._panel_instance)
        container = meta["container"]

        if "top-panel" in container or "bottom-panel" in container:
            return self.p.gtk.Orientation.HORIZONTAL
        return (
            self.p.gtk.Orientation.HORIZONTAL
            if self.p.panel_orientation.lower() == "h"
            else self.p.gtk.Orientation.VERTICAL
        )

    def on_left_click(self, cmd):
        """Runs command and optionally toggles Niri Overview."""
        self.p.cmd.run(cmd)
        if not self.p.layer_always_exclusive and self.p.left_click_toggles_scale:
            # Niri Action: Toggle Overview
            self.p.ipc.sock.close_overview()

    def on_right_click(self, cmd):
        """Runs command or cycles focus to the next output."""
        if not self.p.right_click_to_next_output:
            self.p.cmd.run(cmd)
            return

        try:
            outputs = self.p.ipc.list_outputs()
            focused = self.p.ipc.get_focused_output()

            if not outputs or not focused:
                self.p.cmd.run(cmd)
                return

            # Find index of current output
            idx = next(
                (i for i, o in enumerate(outputs) if o["name"] == focused["name"]), -1
            )

            # Get next output name
            next_output = outputs[(idx + 1) % len(outputs)]
            next_name = next_output["name"]

            # Niri Action: Focus Output by Name
            self.p.ipc.sock.focus_output(next_name)
            self.p.cmd.run(cmd)
        except Exception as e:
            self.p.logger.error(f"Error in right-click action: {e}")

    def on_middle_click(self, cmd):
        """Niri equivalent: Open Overview and run command."""
        if self.p.middle_click_to_empty_workspace:
            # Niri handles empty workspaces dynamically.
            # We toggle overview to allow the user to see the layout.
            self.p.ipc.close_overview()
            # Focus a new workspace on the bottom
            self.p.ipc.focus_workspace(100)
            self.p.cmd.run(cmd)
            return

        self.p.cmd.run(cmd)

    def setup_file_watcher(self):
        """Standard GIO file monitor for config reloads."""
        config_file = self.p.config_handler.config_file
        try:
            self.p.gio_config_file = self.p.gio.File.new_for_path(str(config_file))
            self.p._config_observer = self.p.gio_config_file.monitor_file(
                self.p.gio.FileMonitorFlags.NONE, None
            )
            self.p._config_observer.connect(
                "changed", self.p._on_gio_config_file_changed
            )
            self.p._last_config_mod_time = self.p.os.path.getmtime(config_file)
        except Exception as e:
            self.p.logger.error(f"Dockbar watcher failed: {e}")
