## nbar

Nbar is a lightweight, modular and highly customizable status panel for the [niri](https://github.com/YaLTeR/niri) Wayland compositor. It is written in Python on GTK 4, Libadwaita and gtk4-layer-shell, and it behaves like a small shell: top, bottom, left and right panels with their own styling, and plugins that add widgets (system monitor, app launcher, taskbar, dockbar, clock and more) or handle gestures.

Nbar talks to niri through [pyniri](https://github.com/killown/pyniri) and supports **niri only**.

### Quick start

```sh
git clone https://github.com/killown/nbar.git
cd nbar
python run.py
```

`run.py` needs a running niri session (`NIRI_SOCKET` set) and `libgtk4-layer-shell` installed. It creates a virtualenv, installs the dependencies and starts the panel. Your configuration lives in `~/.config/nbar/config.toml`.

See [docs/ABOUT.md](docs/ABOUT.md) for an overview and [docs/PLUGIN.md](docs/PLUGIN.md) to write your own plugin.

### Maintenance

**Status: solo-maintained / private development.**

> Nbar is maintained for my personal use. Issues and Pull Requests are
> disabled, and I will not review external contributions or support requests.
> You are welcome to fork it.

## License

nbar is licensed under the **AGPLv3 (GNU Affero General Public License version 3)**. [See LICENSE for more information](https://github.com/killown/nbar/blob/main/LICENSE).
