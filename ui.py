"""
Plugin Vault — main application window.

wxPython is used throughout so that all controls map to native Windows widgets,
giving NVDA, JAWS, and Narrator full access to every interactive element.
"""

import threading
import zipfile
import wx

import scanner
import backup as bk
from backup import read_manifest_from_pgz

VERSION = '1.0'

try:
    import prism
    _HAS_PRISM = True
except ImportError:
    _HAS_PRISM = False


class RestoreDialog(wx.Dialog):
    """
    Pick which plugins to import from a backup. Laid out like the main
    window: a checkable plugin list, a data-folders pane for the highlighted
    plugin, and Select All / Select None.
    """

    def __init__(self, parent, pgz_path: str, manifest, info: list):
        import os
        super().__init__(parent, title=f'Import from Backup — {os.path.basename(pgz_path)}',
                         size=(940, 600),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.entries = manifest.plugins
        self.info = info    # [(installed, data_targets)] per entry
        n = len(self.entries)
        n_installed = sum(1 for installed, _ in info if installed)

        summary = (f'This backup was created on {manifest.created[:10]} and contains '
                   f'{n} plugin(s).')
        if n_installed:
            summary += (f' {n_installed} are already installed on this computer and '
                        'will be skipped even if checked.')
        summary_text = wx.StaticText(self, label=summary)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_all  = wx.Button(self, label='Select &All')
        self.btn_none = wx.Button(self, label='Select &None')
        for w in (self.btn_all, self.btn_none):
            btn_row.Add(w, 0, wx.RIGHT, 6)

        # Screen readers use this label as the list's name
        list_label = wx.StaticText(self, label=f'&Plugins in Backup ({n}):')
        self.list_ctrl = wx.ListCtrl(
            self, style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES | wx.BORDER_SUNKEN,
        )
        self.list_ctrl.EnableCheckBoxes(True)
        self.list_ctrl.AppendColumn('Name',       width=220)
        self.list_ctrl.AppendColumn('Type',       width=58)
        self.list_ctrl.AppendColumn('Company',    width=160)
        self.list_ctrl.AppendColumn('Status',     width=190)
        self.list_ctrl.AppendColumn('Restore To', width=300)
        self.list_ctrl.SetName('Plugins in backup')
        for i, e in enumerate(self.entries):
            installed = info[i][0]
            row = self.list_ctrl.InsertItem(i, e.name)
            self.list_ctrl.SetItem(row, 1, e.plugin_type)
            self.list_ctrl.SetItem(row, 2, e.company)
            self.list_ctrl.SetItem(row, 3, 'Installed (will be skipped)' if installed
                                   else 'Not installed')
            self.list_ctrl.SetItem(row, 4, e.plugin_dest_path)
            # Pre-check only what would actually be imported
            self.list_ctrl.CheckItem(row, not installed)

        self.data_label = wx.StaticText(
            self, label='Data Folders:  (select a plugin above to view)')
        self.data_list = wx.ListBox(self, style=wx.LB_SINGLE | wx.BORDER_SUNKEN)
        self.data_list.SetName('Data folders list')

        self.btn_import = wx.Button(self, wx.ID_OK)
        self.btn_cancel = wx.Button(self, wx.ID_CANCEL, label='Cancel')
        self.btn_import.SetDefault()
        bottom = wx.StdDialogButtonSizer()
        bottom.AddButton(self.btn_import)
        bottom.AddButton(self.btn_cancel)
        bottom.Realize()

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(summary_text,    0, wx.ALL | wx.EXPAND, 8)
        outer.Add(btn_row,         0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        outer.Add(list_label,      0, wx.LEFT | wx.RIGHT, 8)
        outer.Add(self.list_ctrl,  3, wx.ALL | wx.EXPAND, 8)
        outer.Add(self.data_label, 0, wx.LEFT | wx.RIGHT, 8)
        outer.Add(self.data_list,  1, wx.ALL | wx.EXPAND, 8)
        outer.Add(bottom,          0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetSizer(outer)
        self.SetMinSize((640, 440))
        summary_text.Wrap(900)

        self.btn_all.Bind(wx.EVT_BUTTON,  lambda _e: self._check_all(True))
        self.btn_none.Bind(wx.EVT_BUTTON, lambda _e: self._check_all(False))
        self.btn_import.Bind(wx.EVT_BUTTON, self._on_import)
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_CHECKED,   lambda _e: self._refresh_count())
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_UNCHECKED, lambda _e: self._refresh_count())
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_SELECTED,  self._on_selected)

        self._refresh_count()
        self.CentreOnParent()
        # Start in the list, on the first plugin, so its name and checkbox state
        # are read out straight away
        self.list_ctrl.SetFocus()
        if n:
            self.list_ctrl.Focus(0)
            self.list_ctrl.Select(0)

    def selected_indices(self) -> set:
        return {i for i in range(self.list_ctrl.GetItemCount())
                if self.list_ctrl.IsItemChecked(i)}

    def _check_all(self, checked: bool):
        for i in range(self.list_ctrl.GetItemCount()):
            self.list_ctrl.CheckItem(i, checked)
        self._refresh_count()

    def _refresh_count(self):
        # The count is in the button's label so it's read out when tabbing to it
        n = len(self.selected_indices())
        self.btn_import.SetLabel(f'&Import Selected ({n})')
        self.Layout()

    def _on_selected(self, event):
        idx = event.GetIndex()
        entry = self.entries[idx]
        targets = self.info[idx][1]
        self.data_list.Clear()
        if targets:
            self.data_label.SetLabel(
                f'Data Folders for {entry.name}:  ({len(targets)} folder(s) will be restored)')
            for t in targets:
                self.data_list.Append(t)
        else:
            self.data_label.SetLabel(f'Data Folders for {entry.name}:  (none in this backup)')
            self.data_list.Append('This backup has no data folders for this plugin.')
        event.Skip()

    def _on_import(self, _event):
        if not self.selected_indices():
            wx.MessageBox('No plugins are checked.\n\n'
                          'Use the checkboxes to choose which plugins to import, '
                          'or press Select All.',
                          'Nothing Selected', wx.OK | wx.ICON_INFORMATION, self)
            return
        self.EndModal(wx.ID_OK)


class PluginVaultFrame(wx.Frame):

    def __init__(self):
        super().__init__(None, title='Plugin Vault', size=(980, 640),
                         style=wx.DEFAULT_FRAME_STYLE)
        self.plugins: list = []
        self._speech = None
        if _HAS_PRISM:
            try:
                self._speech = prism.Context()
            except Exception:
                pass
        self._build_menu()
        self._build_ui()
        self._bind_events()
        self.SetMinSize((640, 440))
        self.Centre()
        self._populate_from_cache()

    # ── menu bar ──────────────────────────────────────────────────────────────

    def _build_menu(self):
        mb = wx.MenuBar()

        file_menu = wx.Menu()
        self.mi_scan    = file_menu.Append(wx.ID_ANY, '&Scan Plugins\tCtrl+R',
                                           'Quick scan — loads known plugins from cache, checks for new ones')
        self.mi_rescan  = file_menu.Append(wx.ID_ANY, '&Full Rescan (Ignore Cache)\tCtrl+Shift+F',
                                           'Ignore the cache and do a complete fresh scan')
        file_menu.AppendSeparator()
        self.mi_backup  = file_menu.Append(wx.ID_ANY, '&Backup Selected…\tCtrl+B',
                                           'Save checked plugins to a .pgz backup file')
        self.mi_restore = file_menu.Append(wx.ID_ANY, 'R&estore from Backup…\tCtrl+Shift+R',
                                           'Restore plugins from a .pgz backup file')
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, 'E&xit\tAlt+F4')
        mb.Append(file_menu, '&File')

        help_menu = wx.Menu()
        help_menu.Append(wx.ID_ABOUT, '&About Plugin Vault')
        mb.Append(help_menu, '&Help')

        self.SetMenuBar(mb)

    # ── main panel ────────────────────────────────────────────────────────────

    def _build_ui(self):
        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        # Button row
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_scan    = wx.Button(panel, label='&Scan Plugins')
        self.btn_backup  = wx.Button(panel, label='&Backup Selected…')
        self.btn_restore = wx.Button(panel, label='&Restore from Backup…')
        sep = wx.StaticLine(panel, style=wx.LI_VERTICAL)
        self.btn_all  = wx.Button(panel, label='Select &All')
        self.btn_none = wx.Button(panel, label='Select &None')

        self.btn_scan.SetToolTip('Scan the system for VST2, VST3, and CLAP plugins (Ctrl+R)')
        self.btn_backup.SetToolTip('Back up checked plugins to a .pgz file (Ctrl+B)')
        self.btn_restore.SetToolTip('Restore plugins from a .pgz backup file (Ctrl+Shift+R)')

        for w in (self.btn_scan, self.btn_backup, self.btn_restore):
            btn_row.Add(w, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        btn_row.Add(sep, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        for w in (self.btn_all, self.btn_none):
            btn_row.Add(w, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        # Splitter: plugins list (top) / data paths (bottom)
        self.splitter = wx.SplitterWindow(
            panel, style=wx.SP_LIVE_UPDATE | wx.SP_3DSASH
        )
        self.splitter.SetMinimumPaneSize(80)

        # ── top pane: plugin list ──────────────────────────────────────────
        top_pane = wx.Panel(self.splitter)
        top_sizer = wx.BoxSizer(wx.VERTICAL)

        # Screen readers use this label as the list's name, so it also carries the count
        self.list_label = wx.StaticText(top_pane, label='Detected Plugins:')

        self.list_ctrl = wx.ListCtrl(
            top_pane,
            style=wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES | wx.BORDER_SUNKEN,
        )
        self.list_ctrl.EnableCheckBoxes(True)
        self.list_ctrl.AppendColumn('Name',        width=230)
        self.list_ctrl.AppendColumn('Type',        width=58)
        self.list_ctrl.AppendColumn('Company',     width=170)
        self.list_ctrl.AppendColumn('Plugin Path', width=380)
        self.list_ctrl.SetName('Plugin list')

        top_sizer.Add(self.list_label, 0, wx.BOTTOM,         4)
        top_sizer.Add(self.list_ctrl, 1, wx.EXPAND)
        top_pane.SetSizer(top_sizer)

        # ── bottom pane: data paths ────────────────────────────────────────
        bot_pane = wx.Panel(self.splitter)
        bot_sizer = wx.BoxSizer(wx.VERTICAL)

        self.data_label = wx.StaticText(
            bot_pane, label='Data Paths:  (select a plugin above to view)'
        )
        self.data_list = wx.ListBox(
            bot_pane,
            style=wx.LB_SINGLE | wx.BORDER_SUNKEN,
        )
        self.data_list.SetName('Data paths list')

        bot_sizer.Add(self.data_label, 0, wx.BOTTOM, 4)
        bot_sizer.Add(self.data_list,  1, wx.EXPAND)
        bot_pane.SetSizer(bot_sizer)

        self.splitter.SplitHorizontally(top_pane, bot_pane, sashPosition=340)

        # Activity log: read-only, reachable with Tab, keeps a history of
        # status messages (the status bar alone isn't in the tab order)
        log_label = wx.StaticText(panel, label='Activity &Log:')
        self.log_ctrl = wx.TextCtrl(
            panel, size=(-1, 90),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.BORDER_SUNKEN,
        )
        self.log_ctrl.SetName('Activity log')

        # Status bar: left = general status, right = selection count
        self.sb = self.CreateStatusBar(2)
        self.sb.SetStatusWidths([-1, 170])

        outer.Add(btn_row,        0, wx.ALL,    8)
        outer.Add(self.splitter,  1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        outer.Add(log_label,      0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        outer.Add(self.log_ctrl,  0, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(outer)

    # ── event binding ─────────────────────────────────────────────────────────

    def _bind_events(self):
        self.btn_scan.Bind(wx.EVT_BUTTON,    self.on_scan)
        self.btn_backup.Bind(wx.EVT_BUTTON,  self.on_backup)
        self.btn_restore.Bind(wx.EVT_BUTTON, self.on_restore)
        self.btn_all.Bind(wx.EVT_BUTTON,     self.on_select_all)
        self.btn_none.Bind(wx.EVT_BUTTON,    self.on_select_none)

        self.Bind(wx.EVT_MENU, self.on_scan,            self.mi_scan)
        self.Bind(wx.EVT_MENU, self.on_full_rescan,     self.mi_rescan)
        self.Bind(wx.EVT_MENU, self.on_backup,          self.mi_backup)
        self.Bind(wx.EVT_MENU, self.on_restore,         self.mi_restore)
        self.Bind(wx.EVT_MENU, lambda _e: self.Close(), id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self.on_about,           id=wx.ID_ABOUT)

        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_CHECKED,    self._on_check_change)
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_check_change)
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_SELECTED,   self._on_plugin_selected)
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._on_plugin_deselected)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self.sb.SetStatusText(msg, 0)
        if self.log_ctrl.GetLastPosition() > 0:
            msg = '\n' + msg
        self.log_ctrl.AppendText(msg)

    def _announce(self, msg: str):
        """Speak msg through the running screen reader, if there is one."""
        if self._speech is None:
            return
        try:
            backend = self._speech.acquire_best()
            # With no screen reader running Prism falls back to plain TTS;
            # stay quiet then rather than talking at sighted users
            if backend.name in ('SAPI', 'OneCore'):
                return
            backend.output(msg)
        except Exception:
            pass

    def _on_check_change(self, event):
        self._refresh_count()
        event.Skip()

    def _on_plugin_selected(self, event):
        idx = event.GetIndex()
        if 0 <= idx < len(self.plugins):
            self._update_data_paths(self.plugins[idx])
        event.Skip()

    def _on_plugin_deselected(self, event):
        if self.list_ctrl.GetSelectedItemCount() == 0:
            self._update_data_paths(None)
        event.Skip()

    def _update_data_paths(self, plugin):
        self.data_list.Clear()
        if plugin is None:
            self.data_label.SetLabel('Data Paths:  (select a plugin above to view)')
            return
        if plugin.data_paths:
            self.data_label.SetLabel(
                f'Data Paths for {plugin.name}:  ({len(plugin.data_paths)} folder(s) detected)'
            )
            for path in plugin.data_paths:
                self.data_list.Append(path)
        else:
            self.data_label.SetLabel(f'Data Paths for {plugin.name}:  (none detected)')
            self.data_list.Append('No associated data folders were found for this plugin.')

    def _refresh_count(self):
        n = sum(1 for i in range(self.list_ctrl.GetItemCount())
                if self.list_ctrl.IsItemChecked(i))
        total = self.list_ctrl.GetItemCount()
        self.sb.SetStatusText(f'{n} of {total} selected', 1)

    def _checked_plugins(self) -> list:
        return [self.plugins[i]
                for i in range(self.list_ctrl.GetItemCount())
                if self.list_ctrl.IsItemChecked(i)]

    def _lock_ui(self, locked: bool):
        enable = not locked
        for btn in (self.btn_scan, self.btn_backup, self.btn_restore,
                    self.btn_all, self.btn_none):
            btn.Enable(enable)
        self.mi_scan.Enable(enable)
        self.mi_rescan.Enable(enable)
        self.mi_backup.Enable(enable)
        self.mi_restore.Enable(enable)

    # ── cache startup load ────────────────────────────────────────────────────

    def _populate_from_cache(self):
        """Fill the list from cache.json at startup — no scanning, instant."""
        import os
        raw = scanner.load_cache()
        if not raw:
            self._set_status('Ready — press Scan Plugins to begin.')
            return
        valid = [p for p in raw.values() if os.path.exists(p.plugin_path)]
        stale = len(raw) - len(valid)
        if not valid:
            msg = 'Cache found but all entries are stale — press Scan Plugins.'
            self._set_status(msg)
            return
        self._fill_list(valid)
        parts = [f'{len(valid)} plugin(s) loaded from cache']
        if stale:
            parts.append(f'{stale} stale entr{"y" if stale == 1 else "ies"} ignored')
        self._set_status(', '.join(parts) + '. Press Scan Plugins to check for new ones.')

    # ── scan ──────────────────────────────────────────────────────────────────

    def on_scan(self, _event=None):
        self._start_scan(use_cache=True)

    def on_full_rescan(self, _event=None):
        self._start_scan(use_cache=False)

    def _start_scan(self, use_cache: bool):
        self._lock_ui(True)
        self.list_ctrl.DeleteAllItems()
        self.plugins = []
        self.list_label.SetLabel('Detected Plugins:')
        self._update_data_paths(None)
        self._set_status(
            'Loading cache…' if use_cache else 'Starting full rescan (cache ignored)…'
        )

        def work():
            result = scanner.scan_all(
                progress_callback=lambda msg: wx.CallAfter(self._set_status, msg),
                use_cache=use_cache,
            )
            wx.CallAfter(self._finish_scan, result)

        threading.Thread(target=work, daemon=True).start()

    def _fill_list(self, plugins: list):
        """Populate the list control from a plugin list (does not touch status bar)."""
        self.plugins = plugins
        self.list_ctrl.DeleteAllItems()
        for p in plugins:
            row = self.list_ctrl.InsertItem(self.list_ctrl.GetItemCount(), p.name)
            self.list_ctrl.SetItem(row, 1, p.plugin_type)
            self.list_ctrl.SetItem(row, 2, p.company)
            self.list_ctrl.SetItem(row, 3, p.plugin_path)
        self.list_ctrl.SetColumnWidth(3, wx.LIST_AUTOSIZE)
        self.list_label.SetLabel(f'Detected Plugins ({len(plugins)} found):')
        self._refresh_count()

    def _finish_scan(self, plugins: list):
        self._fill_list(plugins)
        self._lock_ui(False)
        self._set_status(
            f'{len(plugins)} plugin(s) found. '
            'Check plugins to select them, then press Backup Selected.'
        )
        # The scan button was disabled while scanning, so focus is gone;
        # put it on the list, then speak the result once the screen reader
        # has announced the focus change (otherwise that would cut it off)
        self.list_ctrl.SetFocus()
        if plugins:
            self.list_ctrl.Focus(0)
        wx.CallLater(600, self._announce, f'Scan complete. {len(plugins)} plugins found.')

    # ── select all / none ─────────────────────────────────────────────────────

    def on_select_all(self, _event=None):
        for i in range(self.list_ctrl.GetItemCount()):
            self.list_ctrl.CheckItem(i, True)
        self._refresh_count()

    def on_select_none(self, _event=None):
        for i in range(self.list_ctrl.GetItemCount()):
            self.list_ctrl.CheckItem(i, False)
        self._refresh_count()

    # ── backup ────────────────────────────────────────────────────────────────

    def on_backup(self, _event=None):
        selected = self._checked_plugins()
        if not selected:
            wx.MessageBox(
                'No plugins are checked.\n\n'
                'Use the checkboxes in the list to select which plugins to back up,\n'
                'or press Select All to include every detected plugin.',
                'Nothing Selected', wx.OK | wx.ICON_INFORMATION, self)
            return

        dlg = wx.FileDialog(
            self, 'Save Plugin Vault Backup',
            wildcard='Plugin Vault Backup (*.pgz)|*.pgz',
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        path = dlg.GetPath()
        dlg.Destroy()
        if not path.lower().endswith('.pgz'):
            path += '.pgz'

        self._lock_ui(True)
        pdlg = wx.ProgressDialog(
            'Backing Up Plugins', f'Preparing backup of {len(selected)} plugin(s)…',
            maximum=len(selected), parent=self,
            style=wx.PD_APP_MODAL | wx.PD_ELAPSED_TIME,
        )

        def work():
            def progress(msg, idx, _total):
                wx.CallAfter(pdlg.Update, idx, msg)
            try:
                bk.create_backup(selected, path, progress)
                wx.CallAfter(self._backup_done, pdlg, path, None)
            except Exception as exc:
                wx.CallAfter(self._backup_done, pdlg, path, exc)

        threading.Thread(target=work, daemon=True).start()

    def _backup_done(self, pdlg, path, error):
        pdlg.Destroy()
        self._lock_ui(False)
        if error:
            wx.MessageBox(f'Backup failed:\n\n{error}',
                         'Backup Error', wx.OK | wx.ICON_ERROR, self)
        else:
            wx.MessageBox(
                f'Backup saved successfully to:\n{path}\n\n'
                'Keep this .pgz file safe — it contains all your selected plugins\n'
                'and a manifest of where they should be restored.\n\n'
                'Note: licences and activations are not included. Plugins that use\n'
                'iLok, Waves Central, Native Access or a similar licence manager\n'
                'will need to be re-authorised there after restoring.',
                'Backup Complete', wx.OK | wx.ICON_INFORMATION, self)
            self._set_status(f'Backup saved: {path}')

    # ── restore ───────────────────────────────────────────────────────────────

    def on_restore(self, _event=None):
        dlg = wx.FileDialog(
            self, 'Open Plugin Vault Backup',
            wildcard='Plugin Vault Backup (*.pgz)|*.pgz',
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        path = dlg.GetPath()
        dlg.Destroy()

        try:
            manifest = read_manifest_from_pgz(path)
        except Exception as exc:
            wx.MessageBox(f'Could not read backup file:\n\n{exc}',
                         'Invalid Backup', wx.OK | wx.ICON_ERROR, self)
            return

        n = len(manifest.plugins)
        if n == 0:
            wx.MessageBox('This backup contains no plugins.', 'Empty Backup',
                         wx.OK | wx.ICON_WARNING, self)
            return

        try:
            info = bk.describe_backup(path, manifest)
        except Exception as exc:
            wx.MessageBox(f'Could not read backup file:\n\n{exc}',
                         'Invalid Backup', wx.OK | wx.ICON_ERROR, self)
            return

        pick = RestoreDialog(self, path, manifest, info)
        ok = pick.ShowModal() == wx.ID_OK
        only = pick.selected_indices()
        pick.Destroy()
        if not ok:
            return

        self._lock_ui(True)
        pdlg = wx.ProgressDialog(
            'Restoring Plugins', f'Restoring {len(only)} plugin(s)…',
            maximum=len(only), parent=self,
            style=wx.PD_APP_MODAL | wx.PD_ELAPSED_TIME,
        )

        def work():
            def progress(msg, idx, _total):
                wx.CallAfter(pdlg.Update, idx, msg)
            try:
                restored, skipped, errors = bk.restore_backup(path, progress, only=only)
                wx.CallAfter(self._restore_done, pdlg, restored, skipped, errors)
            except Exception as exc:
                wx.CallAfter(self._restore_done, pdlg, 0, 0, [str(exc)])

        threading.Thread(target=work, daemon=True).start()

    def _restore_done(self, pdlg, restored, skipped, errors):
        pdlg.Destroy()
        self._lock_ui(False)
        msg = (
            f'Restore complete.\n\n'
            f'Restored:  {restored} plugin(s)\n'
            f'Skipped:   {skipped} (already present at destination)'
        )
        if restored:
            msg += ('\n\nIf a plugin asks for activation, re-authorise it in its licence '
                    'manager (iLok, Waves Central, Native Access, etc.).')
        if errors:
            msg += f'\n\nErrors ({len(errors)}):\n'
            msg += '\n'.join(errors[:12])
            if len(errors) > 12:
                msg += f'\n… and {len(errors) - 12} more'
        icon = wx.ICON_WARNING if errors else wx.ICON_INFORMATION
        wx.MessageBox(msg, 'Restore Complete', wx.OK | icon, self)
        self._set_status(
            f'Restore complete — {restored} restored, {skipped} skipped.'
        )

    # ── about ─────────────────────────────────────────────────────────────────

    def on_about(self, _event=None):
        try:
            import wx.adv
            info = wx.adv.AboutDialogInfo()
            info.SetName('Plugin Vault')
            info.SetVersion(VERSION)
            info.SetDescription(
                'Scan, back up, and restore your VST2, VST3, and CLAP plugins.\n\n'
                'Backups are saved as .pgz files — a ZIP archive containing your\n'
                'plugin files and a JSON manifest recording their destinations.\n\n'
                'Fully accessible via NVDA, JAWS, and Windows Narrator.'
            )
            wx.adv.AboutBox(info, self)
        except ImportError:
            wx.MessageBox(
                f'Plugin Vault {VERSION}\n\n'
                'Scan, back up, and restore your VST2, VST3, and CLAP plugins.\n\n'
                'Backups are .pgz files (ZIP + JSON manifest).\n'
                'Fully accessible via NVDA, JAWS, and Windows Narrator.',
                'About Plugin Vault', wx.OK | wx.ICON_INFORMATION, self)
