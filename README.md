<img src="assets/icon.svg" align="right" width="80" height="80" alt="">

# flatfind

Find, install, and remove Flatpak apps from a full-screen terminal picker inspired by Omarchy. Search by app name or ID and see matches as you type.

## Quick start

You’ll need **Flatpak**, **Python 3**, and **fzf 0.64 or later** on your `PATH`. To install from source, clone the repository and copy the executable into `~/.local/bin`.

```sh
git clone https://github.com/wfoyt/flatfind.git
cd flatfind
install -Dm755 flatfind ~/.local/bin/flatfind
```

Make sure `~/.local/bin` is on your `PATH`, then run `flatfind` to browse apps or `flatfind remove` to browse installed apps. Add a search term to either command to prefill the picker.

```sh
flatfind firefox
flatfind remove firefox
```

## Keyboard controls

| Key | Action |
| --- | --- |
| **↑ / ↓** | Move through the results. |
| **Tab** | Mark or unmark the highlighted app. The footer appears while apps are marked. |
| **Enter** | Submit the marked apps, or the highlighted app when none are marked. |
| **Alt+P** | Show or hide the details pane. |
| **Esc** | Close the picker and return to your shell. |

The details pane shows the highlighted app’s ID, source, installation, branch, and architecture, plus its version and summary when available. Flatpak asks for confirmation before making changes.

## Installations and catalogs

By default, `flatfind` searches enabled remotes in the user and default system installations. If the same Flathub app, branch, and architecture are available in both, it appears once. Choose **User** or **System** after pressing **Enter**. `flatfind remove` lists installed copies separately so you can choose which one to uninstall.

| Command | What it does |
| --- | --- |
| `flatfind --user` | Browse apps available to your user installation. |
| `flatfind --system` | Browse apps available to the default system installation. |
| `flatfind --installation work` | Browse a named system installation. |
| `flatfind --runtimes` | Include runtimes in the results. |
| `flatfind --refresh` | Refresh remote catalogs and Flathub display metadata. |
| `flatfind remove --user` | Browse only apps installed for your user. |

Search runs locally with `fzf`. Flathub display names and summaries come from its AppStream catalog and are cached for a day.

Run `flatfind --help` or `flatfind remove --help` to see the available flags.
