# Plugin Vault

A free, open-source VST2 / VST3 / CLAP plugin manager and backup tool for Windows.
Built with Python and wxPython for full screen reader compatibility (NVDA, JAWS, Narrator).

---

## Download

Get the latest Windows build from the [Releases page](https://github.com/tunmi13productions/Plugin-Vault/releases). Unzip it anywhere and run `Plugin Vault.exe`. No installation or Python needed.

Requires Windows 10 or later.

Admin access is needed to read and restore plugins stored in Program Files. If Plugin Vault warns that it isn't running as Administrator, right-click `Plugin Vault.exe` and choose **Run as administrator**.

---

## Running from Source

Requires Python 3.9 or later.

```
git clone https://github.com/tunmi13productions/Plugin-Vault.git
cd Plugin-Vault
pip install -r requirements.txt
python main.py
```

Screen reader announcements use [Prism](https://pypi.org/project/prismatoid/) (`prismatoid`). Plugin Vault still runs without it, just without spoken scan results.

To make a release, push a tag such as `v1.1`. The GitHub Actions workflow builds the Windows binaries with PyInstaller and attaches them to a new release.

---

## The Interface

When the app opens, your plugin list is loaded instantly from the local cache if one exists. No scanning required on every launch.

### Detected Plugins List
Shows every VST2, VST3, and CLAP plugin found on your system. Use the checkboxes to select which plugins to include in a backup. Click or arrow to any plugin to see its detected data folders in the panel below.

### Data Paths Panel
Shows any associated data folders (presets, config, samples) detected for the selected plugin. These are included in backups automatically.

Data folders are found three ways: from the installer's own uninstall log when the plugin was installed with Inno Setup (exact, and followed even if the plugin folder was installed to a custom path or moved later), from a built-in table of known vendors, and by matching folder names against the plugin's company and product.

### Activity Log
A read-only log of status messages (scan progress, results, backup and restore outcomes). Reach it with Tab or Alt+L and use the arrow keys to review earlier messages. The latest message also appears in the status bar (NVDA+End, or Insert+Page Down in JAWS).

When a scan finishes, focus moves to the plugin list, and its label announces how many plugins were found.

### Buttons and Keyboard Shortcuts

| Action | Shortcut | Description |
|---|---|---|
| Scan Plugins | Ctrl+R | Quick scan — loads cache, checks for new plugins |
| Full Rescan | Ctrl+Shift+F | Ignore cache and scan everything from scratch |
| Backup Selected | Ctrl+B | Save checked plugins to a `.pgz` backup file |
| Restore from Backup | Ctrl+Shift+R | Restore plugins from a `.pgz` file |
| Select All | | Check every plugin in the list |
| Select None | | Uncheck every plugin in the list |

---

## What Backups Don't Include

Backups contain plugin files and their data folders, but **not licences or activations**. After restoring, plugins that use a licence manager (iLok, Waves Central, Native Access, Arturia Software Center, etc.) must be re-authorised there.

Waves plugins are loaded through a WaveShell DLL. Backing up the shell alone isn't enough: the `Waves Audio` data folders and a Waves Central licence are also required.

## Backing Up

1. Press **Scan Plugins** or wait for the cache to load on startup.
2. Check the plugins you want to include. Use **Select All** to include everything.
3. Press **Backup Selected** and choose where to save the `.pgz` file.
4. Keep the `.pgz` file somewhere safe — external drive, cloud storage, etc.

A `.pgz` file is a standard ZIP archive containing:
- The plugin files themselves (`.dll`, `.vst3`, `.clap`, etc.)
- Any detected data folders (presets, config)
- A `manifest.json` describing where each item should be restored

---

## Restoring

1. Press **Restore from Backup** and select your `.pgz` file.
2. The Import window lists every plugin in the backup, laid out like the main window. Each has a checkbox (Space toggles it), its type, company, whether it's already installed, and where it will be restored. Arrow to a plugin to see which data folders come with it. Plugins that aren't installed yet start out checked.
3. Use **Select All** (Alt+A) or **Select None** (Alt+N), or check plugins individually, then press **Import Selected** (Enter). The button shows how many plugins are checked. Escape cancels without writing anything.
4. Any plugin or data file that already exists at its destination is skipped — existing files are never overwritten.
5. Plugins are restored to the exact paths recorded in the backup's manifest.

> **Note:** Restoring to Program Files requires Administrator privileges. Right-click `Plugin Vault.exe` and choose "Run as administrator" if a restore fails with a permission error.

---

## Scanning Notes

Plugin Vault scans the following standard locations:

**VST2**
- `C:\Program Files\VSTPlugins`
- `C:\Program Files\Steinberg\VSTPlugins`
- `C:\Program Files\Common Files\VST2`
- `C:\Program Files (x86)\...` (same folders)
- Registry paths registered by your DAW

**VST3** (including vendor subfolders)
- `C:\Program Files\Common Files\VST3`
- `C:\Program Files (x86)\Common Files\VST3`
- `%LOCALAPPDATA%\Programs\Common\VST3` (per-user installs)
- `%APPDATA%\VST3` and `Documents\VST3`

**CLAP**
- `C:\Program Files\Common Files\CLAP`
- `C:\Program Files (x86)\Common Files\CLAP`
- `%LOCALAPPDATA%\Programs\Common\CLAP` (per-user installs)
- `%APPDATA%\CLAP`
- Any folders listed in the `CLAP_PATH` environment variable

It also performs a broad scan of `Program Files` and `Program Files (x86)` to catch plugins installed in their own company folders rather than the standard Common Files locations.

VST2, VST3 and CLAP files are validated by checking their exported functions to avoid false positives from unrelated DLLs.

---

## Cache

After every scan, Plugin Vault saves its plugin list to `%LOCALAPPDATA%\Plugin Vault\cache.json`. On the next launch, your plugin list appears instantly without a full scan. Plugins that have been removed from disk are automatically dropped from the cache.

To force a full rescan and rebuild the cache from scratch:
**File > Full Rescan (Ignore Cache)** or **Ctrl+Shift+F**

---

## Disclaimer

Plugin Vault is provided free of charge, **as-is, with no warranty of any kind**, express or implied.

By using this software you acknowledge and agree that:

- The author(s) are **not liable** for any data loss, corruption, or damage to your system, plugins, presets, projects, or any other files arising from the use or misuse of this software.
- Backup and restore operations copy files on your behalf. It is your responsibility to verify that restored files are correct and complete before relying on them in any project or performance.
- This software does **not** circumvent, remove, or interfere with any plugin licensing or copy protection system. License files, iLok activations, and similar DRM are **not** backed up and must be managed through your plugin vendor's official tools.
- The broad Program Files scan is a heuristic process. Plugin Vault makes a reasonable effort to identify plugin files accurately, but it may occasionally miss plugins or misidentify associated data folders.

Always keep at least one additional backup of your plugins through your vendor's official installer or download portal. Plugin Vault is a convenience tool, not a replacement for proper license management.

**Use at your own risk.**

---

## License

Plugin Vault is released under the [MIT License](LICENSE).
