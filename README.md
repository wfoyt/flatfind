<img src="assets/icon.svg" width="64" height="64" alt="">

# flatfind

An Omarchy-style terminal picker for Flatpak packages. Type part of an app name or ID, move with the arrow keys, and press **Enter** to start installation. Use **Tab** to mark multiple results, **Alt+P** to toggle the details pane, or **Esc** to quit.

## Install

With Python 3, `fzf` (0.64 or later), and Flatpak on your `PATH`, run this from the checkout:

```sh
install -Dm755 flatfind ~/.local/bin/flatfind
```

## Use

```sh
flatfind                       # Browse enabled user and system remotes
flatfind firefox               # Start with a search
flatfind --user                # Browse user remotes only
flatfind --system              # Browse default system remotes only
flatfind --installation work   # Browse a named system installation
flatfind --runtimes             # Include runtimes alongside apps
flatfind --refresh             # Ask Flatpak to refresh the remote catalogs
flatfind remove                # Browse installed apps and uninstall selected ones
flatfind remove --user         # Browse only apps installed for your user
```

Like Omarchy’s package picker, `flatfind` loads the catalog once and uses `fzf` to rank matches locally as you type. The list shows package names. The details pane below shows the highlighted package’s ID, source, branch, architecture, and any available version and description. Flatpak handles installation and confirmation for the selected package.

Flathub display names and summaries come from its AppStream catalog. `flatfind` caches this metadata for a day and refreshes it on demand with `--refresh`. When a Flathub app is available for the same branch and architecture in both scopes, it appears once. Press **Enter** and choose **User** or **System**. If only one scope is available, `flatfind` goes directly to Flatpak’s install prompt.

Both pickers fill the terminal and restore your shell when they close. **Tab** marks or unmarks an app. The footer lists marked apps and disappears when the selection is empty. **Enter** submits the marked apps or, when none are marked, the highlighted app. In `flatfind remove`, the details pane shows the installation each entry belongs to.
