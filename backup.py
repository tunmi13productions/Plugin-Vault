"""
Create and restore .pgz backup archives.

A .pgz file is a standard ZIP archive containing:
  manifest.json          — describes every plugin and where its files live
  plugins/<n>/           — plugin DLL / .vst3 bundle / .clap file
  data/<n>/              — associated data folder (presets, config, etc.),
                           stored once even when several plugins use it

On restore, files are written only if they do not already exist at the
destination, so running restore on a machine that already has a plugin is safe.
"""

import os
import shutil
import zipfile
from pathlib import Path

from manifest import Manifest, PluginEntry, DataArchive


def _zip_path(zf: zipfile.ZipFile, src: Path, arc_prefix: str) -> None:
    """Add a file or entire directory tree into zf under arc_prefix."""
    if src.is_file():
        zf.write(src, arc_prefix + src.name)
    elif src.is_dir():
        for item in sorted(src.rglob('*')):
            if item.is_file():
                rel = item.relative_to(src.parent)
                zf.write(item, arc_prefix + str(rel).replace('\\', '/'))


def _is_within(path: Path, folder: Path) -> bool:
    """True if path is folder or inside it (case-insensitive, as on Windows)."""
    p = os.path.normcase(str(path))
    f = os.path.normcase(str(folder))
    return p == f or p.startswith(f.rstrip(os.sep) + os.sep)


def create_backup(plugins: list, output_path: str, progress_callback=None) -> None:
    """
    Write a .pgz archive for the given list of PluginInfo objects.
    progress_callback(msg, current_index, total) is optional.
    """
    manifest = Manifest()
    total = len(plugins)

    # Several plugins often share a data folder (every u-he plugin points at
    # Documents\u-he), or one plugin's folder sits inside another's. Store each
    # outermost folder once and point every plugin at its part of it.
    roots = []   # outermost existing data folders, as Paths
    for dp_str in sorted({d for p in plugins for d in p.data_paths}, key=len):
        dp = Path(dp_str)
        if dp.exists() and not any(_is_within(dp, r) for r in roots):
            roots.append(dp)
    root_prefix = {}   # root Path -> archive prefix, filled as roots are written

    with zipfile.ZipFile(output_path, 'w',
                         compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for idx, plugin in enumerate(plugins):
            if progress_callback:
                progress_callback(f'Backing up: {plugin.name}', idx, total)

            arc_prefix = f'plugins/{idx}/'
            src = Path(plugin.plugin_path)

            if src.exists():
                _zip_path(zf, src, arc_prefix)

            data_archives = []
            for dp_str in plugin.data_paths:
                dp = Path(dp_str)
                root = next((r for r in roots if _is_within(dp, r)), None)
                if root is None:
                    continue    # folder no longer exists
                if root not in root_prefix:
                    root_prefix[root] = f'data/{len(root_prefix)}/'
                    _zip_path(zf, root, root_prefix[root])
                prefix = root_prefix[root]
                if dp == root:
                    data_archives.append(DataArchive(
                        archive_path=prefix,
                        dest_path=str(dp.parent),   # parent so the folder itself is restored
                    ))
                else:
                    # Subfolder of a shared root: restore just that part of it
                    rel = dp.relative_to(root).as_posix()
                    data_archives.append(DataArchive(
                        archive_path=f'{prefix}{root.name}/{rel}/',
                        dest_path=str(dp),
                    ))

            manifest.plugins.append(PluginEntry(
                name=plugin.name,
                company=plugin.company,
                description=plugin.description,
                plugin_type=plugin.plugin_type,
                plugin_archive_path=arc_prefix,
                plugin_dest_path=str(Path(plugin.plugin_path).parent),
                data_archives=data_archives,
            ))

        zf.writestr('manifest.json', manifest.to_json())


def _is_installed(entry, arc_files: list) -> bool:
    """True if every top-level item of the plugin (e.g. "Serum.dll" or
    "Serum.vst3") already exists at its destination."""
    prefix = entry.plugin_archive_path
    top_items = {n[len(prefix):].split('/')[0] for n in arc_files}
    return all((Path(entry.plugin_dest_path) / item).exists() for item in top_items)


def _arc_files(all_names, prefix: str) -> list:
    return [n for n in all_names if n.startswith(prefix) and not n.endswith('/')]


def describe_backup(pgz_path: str, manifest: Manifest) -> list:
    """
    For each plugin in the manifest, return (installed, data_targets):
    whether it already exists on this machine, and the folders its data
    would be restored to.
    """
    with zipfile.ZipFile(pgz_path, 'r') as zf:
        all_names = zf.namelist()
    out = []
    for entry in manifest.plugins:
        arc = _arc_files(all_names, entry.plugin_archive_path)
        installed = bool(arc) and _is_installed(entry, arc)
        targets = []
        for da in entry.data_archives:
            if da.archive_path.strip('/').count('/') == 1:
                # Whole folder ("data/3/"): it is restored into dest_path
                tops = {n[len(da.archive_path):].split('/')[0]
                        for n in _arc_files(all_names, da.archive_path)}
                targets += [str(Path(da.dest_path) / t) for t in sorted(tops)]
            else:
                # Part of a shared folder: restored to dest_path itself
                targets.append(da.dest_path)
        out.append((installed, targets))
    return out


def restore_backup(pgz_path: str, progress_callback=None, only=None) -> tuple:
    """
    Restore plugins from a .pgz archive.
    only: optional set of manifest plugin indices to restore (default: all).

    Returns (restored_count, skipped_count, errors_list).
    A plugin is skipped if its top-level item already exists at the destination.
    Data files are skipped individually if they already exist.
    """
    restored = 0
    skipped = 0
    errors = []

    with zipfile.ZipFile(pgz_path, 'r') as zf:
        try:
            manifest_text = zf.read('manifest.json').decode('utf-8')
        except KeyError:
            raise ValueError('Not a valid Plugin Vault backup: manifest.json is missing.')

        manifest = Manifest.from_json(manifest_text)
        all_names = set(zf.namelist())
        chosen = [(i, e) for i, e in enumerate(manifest.plugins)
                  if only is None or i in only]
        total = len(chosen)

        for idx, (_i, entry) in enumerate(chosen):
            if progress_callback:
                progress_callback(f'Restoring: {entry.name}', idx, total)

            dest_dir = Path(entry.plugin_dest_path)
            prefix = entry.plugin_archive_path

            arc_files = _arc_files(all_names, prefix)

            if not arc_files or _is_installed(entry, arc_files):
                skipped += 1
                continue

            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                for arc_name in arc_files:
                    rel = arc_name[len(prefix):]
                    dest_file = dest_dir / rel
                    if not dest_file.exists():
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(arc_name) as src, open(dest_file, 'wb') as dst:
                            shutil.copyfileobj(src, dst)

                for da in entry.data_archives:
                    da_files = [n for n in all_names
                                if n.startswith(da.archive_path) and not n.endswith('/')]
                    da_dest = Path(da.dest_path)
                    for arc_name in da_files:
                        rel = arc_name[len(da.archive_path):]
                        dest_file = da_dest / rel
                        if not dest_file.exists():
                            dest_file.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(arc_name) as src, open(dest_file, 'wb') as dst:
                                shutil.copyfileobj(src, dst)

                restored += 1
            except Exception as exc:
                errors.append(f'{entry.name}: {exc}')

    return restored, skipped, errors


def read_manifest_from_pgz(pgz_path: str) -> Manifest:
    """Read and return the manifest from a .pgz without extracting anything."""
    with zipfile.ZipFile(pgz_path, 'r') as zf:
        try:
            text = zf.read('manifest.json').decode('utf-8')
        except KeyError:
            raise ValueError('Not a valid Plugin Vault backup: manifest.json is missing.')
    return Manifest.from_json(text)
