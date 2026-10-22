"""Plugin Vault — entry point."""

import sys
import ctypes
import wx
from ui import PluginVaultFrame


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def main():
    app = wx.App()

    if not _is_admin():
        dlg = wx.MessageDialog(
            None,
            'Plugin Vault is not running as Administrator.\n\n'
            'Scanning and restoring plugins in Program Files requires '
            'Administrator privileges. Some plugins may not appear, and '
            'restore operations may fail without elevation.\n\n'
            'To run as Administrator: right-click Plugin Vault and choose '
            '"Run as administrator".\n\n'
            'Continue without Administrator privileges?',
            'Administrator Privileges Recommended',
            wx.YES_NO | wx.YES_DEFAULT | wx.ICON_WARNING,
        )
        result = dlg.ShowModal()
        dlg.Destroy()
        if result != wx.ID_YES:
            return

    frame = PluginVaultFrame()
    frame.Show()
    app.MainLoop()


if __name__ == '__main__':
    main()
