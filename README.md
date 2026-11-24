<img src="assets/icon.svg" align="right" width="64" height="64" alt="">

# Flatfind

An Omarchy plugin for finding, installing, and removing Flatpak apps. Flatfind keeps the **Apps** menu up to date when you install or remove apps through the picker or the Flatpak CLI.

## Install the plugin

Flatfind requires **Omarchy 4.5+**, **Flatpak**, **Python 3**, and **fzf 0.64+**.

Install any missing dependencies.

```sh
omarchy pkg add flatpak python fzf
```

In your Omarchy session, install and enable Flatfind.

```sh
omarchy plugin add https://github.com/wfoyt/flatfind.git --enable
```

From an existing local checkout, you can install the current copy by running `./install` from the project directory.

## Use Flatfind

Open the Omarchy menu and choose **Install → Flatpak** to find apps or **Remove → Flatpak** to uninstall them. Type to search, press **Tab** to select multiple apps, and press **Enter** to continue.

![Flatfind searching for photo apps with two results selected and a details pane below the list.](assets/screenshots/install.png)

## Uninstall the plugin

Disable Flatfind, clean up its launcher links and login integration, then remove the plugin.

```sh
omarchy plugin disable wfo.flatfind &&
  python3 "$HOME/.config/omarchy/plugins/wfo.flatfind/omarchy/desktop.py" --cleanup &&
  omarchy plugin remove wfo.flatfind
```
