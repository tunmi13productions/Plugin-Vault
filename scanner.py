"""
Scan the system for VST2, VST3, and CLAP plugins and attempt to locate
their associated data folders (presets, samples, config) in common locations.
"""

import fnmatch
import glob
import json
import os
import re
import struct
import winreg
from pathlib import Path
from dataclasses import dataclass, field

import innolog

try:
    import win32api
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

try:
    import pefile
    _HAS_PEFILE = True
except ImportError:
    _HAS_PEFILE = False


@dataclass
class PluginInfo:
    name: str
    company: str
    description: str
    plugin_type: str        # 'VST2', 'VST3', 'CLAP'
    plugin_path: str        # absolute path to the DLL / .vst3 bundle / .clap file
    data_paths: list = field(default_factory=list)   # associated data dirs


# Per-user and always writable, unlike the app folder (which may be in
# Program Files, or inside a packaged build)
CACHE_PATH = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Plugin Vault' / 'cache.json'
CACHE_VERSION = 2   # 2: data-path matching rewritten, old entries are stale


def load_cache() -> dict:
    """Return {lower_path: PluginInfo} from cache.json, or {} on any error."""
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding='utf-8'))
        if data.get('version') != CACHE_VERSION:
            return {}
        out = {}
        for item in data.get('plugins', []):
            p = PluginInfo(
                name=item.get('name', ''),
                company=item.get('company', ''),
                description=item.get('description', ''),
                plugin_type=item.get('plugin_type', ''),
                plugin_path=item.get('plugin_path', ''),
                data_paths=item.get('data_paths', []),
            )
            if p.plugin_path:
                out[p.plugin_path.lower()] = p
        return out
    except Exception:
        return {}


def save_cache(plugins: list) -> None:
    """Persist the current plugin list to cache.json. Failure is silent."""
    data = {
        'version': CACHE_VERSION,
        'plugins': [
            {
                'name': p.name,
                'company': p.company,
                'description': p.description,
                'plugin_type': p.plugin_type,
                'plugin_path': p.plugin_path,
                'data_paths': p.data_paths,
            }
            for p in plugins
        ],
    }
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8'
        )
    except Exception:
        pass


# ── environment paths ─────────────────────────────────────────────────────────

def _env(var, default):
    return os.environ.get(var, default)

PF      = _env('ProgramFiles',      r'C:\Program Files')
PF86    = _env('ProgramFiles(x86)', r'C:\Program Files (x86)')
PDATA   = _env('ProgramData',       r'C:\ProgramData')
APPDATA = _env('APPDATA',           str(Path.home() / 'AppData' / 'Roaming'))
LAPPDATA= _env('LOCALAPPDATA',      str(Path.home() / 'AppData' / 'Local'))


def _documents_dir() -> str:
    """The user's real Documents folder (may be redirected, e.g. to OneDrive)."""
    try:
        from win32com.shell import shell, shellcon
        return shell.SHGetFolderPath(0, shellcon.CSIDL_PERSONAL, None, 0)
    except Exception:
        return str(Path.home() / 'Documents')

DOCS = _documents_dir()

VST2_PATHS = [
    os.path.join(PF,   'VSTPlugins'),
    os.path.join(PF,   'Steinberg', 'VSTPlugins'),
    os.path.join(PF,   'Common Files', 'VST2'),
    os.path.join(PF86, 'VSTPlugins'),
    os.path.join(PF86, 'Steinberg', 'VSTPlugins'),
    os.path.join(PF86, 'Common Files', 'VST2'),
    os.path.join(DOCS, 'VST Plugins'),
]

VST3_PATHS = [
    os.path.join(PF,       'Common Files', 'VST3'),
    os.path.join(PF86,     'Common Files', 'VST3'),
    os.path.join(LAPPDATA, 'Programs', 'Common', 'VST3'),   # per-user (VST3 spec)
    os.path.join(APPDATA,  'VST3'),
    os.path.join(DOCS,     'VST3'),
]

CLAP_PATHS = [
    os.path.join(PF,       'Common Files', 'CLAP'),
    os.path.join(PF86,     'Common Files', 'CLAP'),
    os.path.join(LAPPDATA, 'Programs', 'Common', 'CLAP'),   # per-user (CLAP spec)
    os.path.join(APPDATA,  'CLAP'),
] + [p for p in _env('CLAP_PATH', '').split(';') if p]

_DATA_ROOTS = [PF, PF86, PDATA, APPDATA, LAPPDATA, DOCS]


# ── DLL version info ──────────────────────────────────────────────────────────

def _dll_info(path: str) -> dict:
    """Return CompanyName / ProductName / FileDescription from PE version resource."""
    if not _HAS_WIN32 or not os.path.isfile(path):
        return {}
    try:
        trans = win32api.GetFileVersionInfo(path, r'\VarFileInfo\Translation')
        if not trans:
            return {}
        lang, cp = trans[0]
        prefix = f'\\StringFileInfo\\{lang:04X}{cp:04X}\\'
        out = {}
        for key in ('CompanyName', 'ProductName', 'FileDescription'):
            try:
                val = win32api.GetFileVersionInfo(path, prefix + key)
                if val:
                    out[key] = val.strip()
            except Exception:
                pass
        return out
    except Exception:
        return {}


# ── data path discovery ───────────────────────────────────────────────────────

def _clean(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', s.lower())


# Known vendors: (company-name aliases after _clean, folder globs).
# The globs are looked for in _VENDOR_ROOTS. This catches vendors whose
# names are too short for fuzzy matching (u-he, UVI, D16) or whose data
# folder doesn't match the DLL's CompanyName (Xfer Records → Xfer,
# TAL Software → ToguAudioLine). Missing folders are simply skipped.
_VENDORS = [
    (('nativeinstruments', 'ni'),                ['Native Instruments']),
    (('arturia',),                               ['Arturia']),
    (('fabfilter',),                             ['FabFilter']),
    (('xferrecords', 'xfer'),                    ['Xfer']),
    (('uhe', 'uhegmbh'),                         ['u-he']),
    (('izotope',),                               ['iZotope']),
    (('waves', 'wavesaudio'),                    ['Waves Audio', 'Waves']),
    (('valhalladsp', 'valhalla'),                ['Valhalla*']),
    (('kilohearts',),                            ['Kilohearts']),
    (('spectrasonics',),                         ['Spectrasonics']),
    (('toontrack',),                             ['Toontrack']),
    (('spitfireaudio', 'spitfire'),              ['Spitfire Audio']),
    (('ikmultimedia',),                          ['IK Multimedia']),
    (('pluginalliance', 'brainworx'),            ['Plugin Alliance', 'Brainworx']),
    (('softube',),                               ['Softube']),
    (('soundtoys',),                             ['Soundtoys']),
    (('steinberg', 'steinbergmediatechnologies'), ['Steinberg']),
    (('imageline',),                             ['Image-Line']),
    (('uvi',),                                   ['UVI']),
    (('d16group', 'd16'),                        ['D16 Group']),
    (('talsoftware', 'tal', 'toguaudioline'),    ['ToguAudioLine']),
    (('vital', 'vitalaudio'),                    ['Vital']),
    (('surgesynthteam',),                        ['Surge XT']),
    (('eventide',),                              ['Eventide']),
    (('universalaudio',),                        ['Universal Audio']),
    (('slatedigital',),                          ['Slate Digital']),
    (('celemony',),                              ['Celemony']),
    (('sonible',),                               ['sonible']),
    (('appliedacousticssystems', 'aas'),         ['Applied Acoustics Systems']),
    (('refx',),                                  ['reFX']),
    (('robpapen',),                              ['Rob Papen*']),
    (('cableguys',),                             ['Cableguys']),
    (('sugarbytes',),                            ['Sugar Bytes']),
    (('korg', 'korginc'),                        ['KORG']),
    (('roland', 'rolandcorporation'),            ['Roland*']),
    (('meldaproduction',),                       ['MeldaProduction']),
    (('acusticaaudio',),                         ['Acustica*']),
    (('nugenaudio',),                            ['NUGEN Audio']),
    (('overloud',),                              ['Overloud']),
    (('neuraldsp', 'neuraldsptechnologies'),     ['Neural DSP']),
    (('positivegrid',),                          ['Positive Grid']),
    # Seen in a real collection, with matching data folders on disk
    (('antares', 'antaresaudiotechnologies'),    ['Antares']),
    (('tokyodawnlabs', 'tokyodawnrecords', 'tdr'), ['Tokyo Dawn*']),
    (('maizesoft',),                             ['Maize*']),
    (('fkfx', 'nicolaslacoumettefkfx'),          ['FKFX']),
    (('flux', 'fluxsoundandpicturedevelopment'), ['Flux']),
    (('dearreality',),                           ['DearReality', 'Dear Reality']),
    (('kazrog',),                                ['Kazrog']),
    (('tritik',),                                ['Tritik']),
    (('echosoundworks',),                        ['Echo Sound Works']),
    (('sonicanomaly',),                          ['Sonic Anomaly']),
    (('producersources',),                       ['Producersources']),
    # Presets go to ProgramData\Cymatics\<Product>\Presets (per installer logs)
    (('cymatics',),                              ['Cymatics']),
]

_VENDOR_ROOTS = [DOCS, PDATA, APPDATA, LAPPDATA]

# Standard VST3 preset folders: <root>\VST3 Presets\<Vendor>\<Plugin>
_VST3_PRESET_ROOTS = [
    os.path.join(DOCS,    'VST3 Presets'),
    os.path.join(APPDATA, 'VST3 Presets'),
    os.path.join(PDATA,   'VST3 Presets'),
]

# Legal-form words that CompanyName often carries but folder names don't
_LEGAL_WORDS = {'co', 'company', 'gmbh', 'inc', 'llc', 'ltd', 'limited', 'corp',
                'corporation', 'srl', 'sa', 'ag', 'kg', 'bv', 'pty', 'plc'}

# Folders a plugin may sit in that say nothing about its vendor
_NON_VENDOR_DIRS = {
    'x64', 'x86', 'win32', 'win64', '32bit', '64bit', 'x8664win', 'x86win',
    'vst', 'vst2', 'vst3', 'vstplugins', 'vstplugin', 'vst64', 'vst32', 'clap',
    'plugins', 'plugin', 'lib', 'bin', 'contents', 'commonfiles', 'programfiles',
    'programfilesx86', 'steinberg', 'music', 'audio',
    'windows', 'win', 'pc', 'release', 'build', 'dist', 'gui', 'alternativegui',
}

# OS folders that fuzzy matching must never pick up ("windows" ⊂ "windowsnt")
_SYSTEM_PREFIXES = ('windows', 'microsoft', 'modifiablewindowsapps', 'internetexplorer')


def tidy_company(raw: str) -> str:
    """CompanyName without emails, URLs or trailing '/ contact' parts."""
    s = re.sub(r'\S+@\S+|https?://\S+|www\.\S+', '', raw)
    s = re.split(r'[/|]', s)[0]
    s = re.sub(r'(?i)copyright|\(c\)|©', '', s)
    return re.sub(r'\s+', ' ', s).strip(' ,.-:')


def _company_key(company: str) -> str:
    """Cleaned company name with legal-form words dropped from the end."""
    words = re.findall(r'[a-z0-9]+', company.lower())
    while len(words) > 1 and words[-1] in _LEGAL_WORDS:
        words.pop()
    return ''.join(words)


def _folder_hint(plugin_path: str) -> str:
    """Nearest parent folder that might be a vendor or product name, or ''."""
    parent = Path(plugin_path).parent
    for _ in range(2):
        if parent == parent.parent:   # reached the drive root
            break
        if _clean(parent.name) not in _NON_VENDOR_DIRS:
            return parent.name
        parent = parent.parent
    return ''

# Folder names too generic to count as a company match ("Foo Audio" ⊅ "Audio")
_GENERIC_FOLDERS = {
    'audio', 'sound', 'sounds', 'music', 'studio', 'media', 'digital', 'software',
    'plugins', 'presets', 'samples', 'records', 'systems', 'instruments',
    'technologies', 'vstplugins', 'commonfiles', 'programs', 'packages',
}


def _list_dirs(root: str) -> list:
    """Return the subdirectories of root, listed once per scan."""
    if root not in _dir_cache:
        try:
            _dir_cache[root] = [e for e in os.scandir(root) if e.is_dir()]
        except (PermissionError, OSError):
            _dir_cache[root] = []
    return _dir_cache[root]

_dir_cache: dict = {}


def _vendor_globs(cc: str) -> list:
    for aliases, globs in _VENDORS:
        for alias in aliases:
            # Short aliases (ni, tal, uvi) must match exactly; longer ones
            # may also prefix the name ("nativeinstrumentsgmbh")
            if cc == alias or (len(alias) >= 5 and cc.startswith(alias)):
                return globs
    return []


def _narrow_to_product(folder: str, pc: str) -> list:
    """Product subfolders of a vendor folder if any match, else the folder itself."""
    if pc and len(pc) >= 4:
        hits = [e.path for e in _list_dirs(folder) if pc in _clean(e.name)]
        if hits:
            return hits
    return [folder]


def _find_data_paths(company: str, product: str, hint: str = '') -> list:
    """
    hint: a vendor/product guess (e.g. the plugin's install folder name),
    used in place of the company when the DLL doesn't name one.
    """
    if not company and hint:
        company = hint
    cc = _clean(company)
    pc = _clean(product)
    if not cc and not pc:
        return []
    cc_base = _company_key(company) or cc

    results = []

    # 1. Known vendor folders
    vendor_globs = _vendor_globs(cc) or _vendor_globs(cc_base)
    for root in _VENDOR_ROOTS:
        for pattern in vendor_globs:
            for e in _list_dirs(root):
                if fnmatch.fnmatch(e.name.lower(), pattern.lower()):
                    results += _narrow_to_product(e.path, pc)

    # 2. Standard VST3 preset folders, matched by vendor name
    for root in _VST3_PRESET_ROOTS:
        for e in _list_dirs(root):
            ec = _clean(e.name)
            if len(ec) >= 3 and cc_base and (ec == cc_base or ec.startswith(cc_base)
                                             or cc_base.startswith(ec)):
                results += _narrow_to_product(e.path, pc)

    # 3. Fuzzy match on top-level folder names
    for root in _DATA_ROOTS:
        for e in _list_dirs(root):
            ec = _clean(e.name)
            if ec.startswith(_SYSTEM_PREFIXES) and not cc.startswith('microsoft'):
                continue
            # Company match is bidirectional, but both sides must be long
            # enough that generic folders ("Audio", "Apps") can't match.
            # Skipped for known vendors: step 1 already found their folders.
            # Product match: only allow pc-in-ec (folder contains product name).
            # Allowing ec-in-pc caused "notepad" (Notepad++ folder) to match
            # "mnotepad" (MNotepad plugin) because "notepad" ⊂ "mnotepad".
            company_match = (not vendor_globs and len(cc_base) >= 5 and len(ec) >= 5
                             and ec not in _GENERIC_FOLDERS
                             and (cc_base in ec or ec in cc_base))
            product_match = len(pc) >= 5 and pc in ec
            if company_match or product_match:
                results.append(e.path)

    return _outermost(results)


def _outermost(paths: list) -> list:
    """Drop duplicates and folders already covered by a parent in the list."""
    unique = []
    for p in sorted(set(paths), key=len):
        pn = os.path.normcase(p)
        if not any(pn == os.path.normcase(u) or pn.startswith(os.path.normcase(u) + os.sep)
                   for u in unique):
            unique.append(p)
    return unique


# ── Inno Setup uninstall logs ─────────────────────────────────────────────────
#
# Inno Setup installers leave unins000.dat next to their uninstaller, listing
# every file and folder they created — including preset and data folders.
# When a plugin came from such an installer, this is better than any guess.

# Logged folders under these are program files or OS folders, never plugin data
_INNO_EXCLUDED_ROOTS = [
    _env('SystemRoot', r'C:\Windows'), PF, PF86,
    os.path.join(str(Path.home()), 'Desktop'),
    os.path.join(_env('PUBLIC', r'C:\Users\Public'), 'Desktop'),
]
_BUNDLE_EXTS = ('.vst3', '.vst', '.clap', '.aaxplugin', '.component', '.bundle')
_USER_PROFILE = re.compile(r'^[a-z]:\\users\\[^\\]+(?=\\|$)', re.I)

_inno_logs: dict = {}        # .dat path (normcased) -> InnoLog, or None if unreadable
_inno_by_path: dict = None   # installed path (normcased) -> [InnoLog]; built once per scan


def _load_inno_log(dat: str):
    key = os.path.normcase(dat)
    if key not in _inno_logs:
        try:
            _inno_logs[key] = innolog.read_log(dat)
        except (OSError, ValueError, struct.error):
            _inno_logs[key] = None
    return _inno_logs[key]


def _registry_inno_logs() -> list:
    """unins*.dat paths of every Inno Setup install registered with Windows."""
    out = []
    uninstall = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    views = [
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER,  0),
    ]
    for hive, view in views:
        try:
            with winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | view) as k:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    if not name.endswith('_is1'):   # Inno Setup's key suffix
                        continue
                    try:
                        with winreg.OpenKey(k, name) as sub:
                            exe = winreg.QueryValueEx(sub, 'UninstallString')[0]
                    except OSError:
                        continue
                    exe = exe.strip()
                    exe = exe[1:exe.find('"', 1)] if exe.startswith('"') else exe
                    out.append(os.path.splitext(exe)[0] + '.dat')
        except OSError:
            pass
    return out


def _inno_index() -> dict:
    global _inno_by_path
    if _inno_by_path is None:
        _inno_by_path = {}
        for dat in dict.fromkeys(_registry_inno_logs()):
            log = _load_inno_log(dat)
            if log:
                for p in log.files + log.dirs:
                    _inno_by_path.setdefault(os.path.normcase(p), []).append(log)
    return _inno_by_path


_nearby_cache: dict = {}   # folder (normcased) -> unins*.dat paths in it and its subfolders


def _nearby_inno_logs(folder: str) -> list:
    """unins*.dat in folder and its direct subfolders, where installers often
    put their uninstaller beside the plugin ("Plugin.dll" + "Plugin\\unins000.dat")."""
    key = os.path.normcase(folder)
    if key not in _nearby_cache:
        found = glob.glob(os.path.join(glob.escape(folder), 'unins*.dat'))
        for e in _list_dirs(folder):
            if not e.name.lower().endswith(_BUNDLE_EXTS):
                found += glob.glob(os.path.join(glob.escape(e.path), 'unins*.dat'))
        _nearby_cache[key] = found
    return _nearby_cache[key]


def _inno_logs_for(plugin_path: str) -> list:
    logs = list(_inno_index().get(os.path.normcase(plugin_path), []))
    # Logs lying near the plugin, e.g. in a plugin folder copied from another
    # PC: paths in them may not match, so compare file names instead
    name = os.path.basename(os.path.normcase(plugin_path))
    parent = os.path.dirname(plugin_path)
    for folder in dict.fromkeys((parent, os.path.dirname(parent))):
        for dat in _nearby_inno_logs(folder):
            log = _load_inno_log(dat)
            if log and all(log is not l for l in logs) and any(
                    os.path.basename(os.path.normcase(p)) == name
                    for p in log.files + log.dirs):
                logs.append(log)
    return logs


def _within(path_n: str, folder: str) -> bool:
    """path_n (normcased) is folder or inside it."""
    f = os.path.normcase(folder).rstrip(os.sep)
    return path_n == f or path_n.startswith(f + os.sep)


def _locate(logged: str, log) -> str:
    """
    Where a folder named in a log is now, or '' if it can't be found.
    Installs often used custom paths, or the plugin folder has since been
    moved or copied from another PC, so the logged path is only a start.
    """
    if os.path.isdir(logged):
        return logged
    ln = os.path.normcase(logged)
    # Inside the install folder: re-anchor on where the log file is now
    dat_dir = os.path.dirname(log.source)
    if log.app_dir and _within(ln, log.app_dir) and dat_dir:
        moved = dat_dir + logged[len(log.app_dir.rstrip(os.sep)):]
        if os.path.isdir(moved):
            return moved
    # In someone's user profile (other user name / other PC): try ours
    m = _USER_PROFILE.match(logged)
    if m:
        mine = str(Path.home()) + logged[m.end():]
        if os.path.isdir(mine):
            return mine
    return ''


def _inno_data_paths(plugin_path: str) -> list:
    """Data folders the plugin's installer created, if it was an Inno Setup one."""
    plugin_n = os.path.normcase(plugin_path)
    roots_n = {os.path.normcase(r) for r in _DATA_ROOTS}
    results = []
    for log in _inno_logs_for(plugin_path):
        app_dirs = [log.app_dir, _locate(log.app_dir, log)] if log.app_dir else []
        for logged in dict.fromkeys(log.dirs + [os.path.dirname(f) for f in log.files]):
            c = _locate(logged, log)
            if not c:
                continue
            cn = os.path.normcase(c)
            parts = cn.split(os.sep)
            if (cn in roots_n or len(parts) < 2 or not parts[1]       # data root / drive root
                    or any(_within(cn, r) for r in _INNO_EXCLUDED_ROOTS)
                    or 'start menu' in parts
                    or any(p.endswith(_BUNDLE_EXTS) for p in parts)):  # inside a plugin bundle
                continue
            if any(cn.startswith(r + os.sep) and _clean(cn[len(r) + 1:].split(os.sep)[0])
                   .startswith(_SYSTEM_PREFIXES) for r in roots_n):
                continue                                    # e.g. ProgramData\Microsoft
            if any(a and _within(os.path.normcase(a), c) for a in app_dirs):
                continue                                    # the install folder or above it
            if _within(plugin_n, c):
                continue                                    # holds the plugin itself
            results.append(c)
    return _outermost(results)


# ── plugin factories ──────────────────────────────────────────────────────────

def _moduleinfo_vendor(bundle: str) -> str:
    """Vendor declared in a VST3 bundle's moduleinfo.json (VST3 SDK 3.7.5+), or ''."""
    path = os.path.join(bundle, 'Contents', 'Resources', 'moduleinfo.json')
    try:
        text = Path(path).read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return ''
    # The file is JSON5 (comments, trailing commas allowed), so don't json.loads it
    m = re.search(r'"Factory Info"\s*:\s*\{[^}]*?"Vendor"\s*:\s*"([^"]*)"', text, re.S)
    return m.group(1).strip() if m else ''


def _make_plugin(plugin_path: str, ptype: str, dll_for_info: str = None) -> PluginInfo:
    info = _dll_info(dll_for_info or plugin_path)
    stem = Path(plugin_path).stem
    vendor = _moduleinfo_vendor(plugin_path) if os.path.isdir(plugin_path) else ''
    # Prefer the bundle's own vendor field; DLL CompanyName is often blank or generic
    company     = tidy_company(vendor or info.get('CompanyName', ''))
    product     = info.get('ProductName', '') or stem
    description = info.get('FileDescription', '')
    return PluginInfo(
        name=product,
        company=company,
        description=description,
        plugin_type=ptype,
        plugin_path=plugin_path,
        # Over half of freeware plugins have no CompanyName at all; their
        # install folder ("SONIC ANOMALY\x64\...") is then the best clue
        data_paths=_outermost(
            _find_data_paths(company, product, hint=_folder_hint(plugin_path))
            + _inno_data_paths(plugin_path)
        ),
    )


def _vst3_inner_dll(bundle: str) -> str:
    """Return the inner .dll path inside a .vst3 bundle folder, or ''."""
    contents = os.path.join(bundle, 'Contents')
    if not os.path.isdir(contents):
        return ''
    for arch in ('x86_64-win', 'arm64-win', 'x86-win'):
        arch_dir = os.path.join(contents, arch)
        if os.path.isdir(arch_dir):
            try:
                for f in os.scandir(arch_dir):
                    if f.name.lower().endswith(('.dll', '.vst3')):
                        return f.path
            except (PermissionError, OSError):
                pass
    # fall back: any subdir
    try:
        for entry in os.scandir(contents):
            if entry.is_dir():
                for f in os.scandir(entry.path):
                    if f.name.lower().endswith(('.dll', '.vst3')):
                        return f.path
    except (PermissionError, OSError):
        pass
    return ''


# ── PE export validation ──────────────────────────────────────────────────────

def _dll_exports(path: str) -> set:
    """Return the set of exported symbol names from a PE file, or empty set on error."""
    if not _HAS_PEFILE:
        return set()
    try:
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_EXPORT']]
        )
        if not hasattr(pe, 'DIRECTORY_ENTRY_EXPORT'):
            pe.close()
            return set()
        names = {
            sym.name.decode('ascii', errors='ignore')
            for sym in pe.DIRECTORY_ENTRY_EXPORT.symbols
            if sym.name
        }
        pe.close()
        return names
    except Exception:
        return set()


def _is_vst2_dll(path: str) -> bool:
    """VST2 plugins must export VSTPluginMain (VST 2.4+) or the older 'main'."""
    if not _HAS_PEFILE:
        return True     # pefile not installed — accept all DLLs (old behaviour)
    exports = _dll_exports(path)
    if not exports:
        return False    # no exports at all → definitely not a plugin
    return bool(exports & {'VSTPluginMain', 'main', 'MAIN'})


def _is_vst3_dll(path: str) -> bool:
    """VST3 plugins must export GetPluginFactory."""
    if not _HAS_PEFILE:
        return True
    return 'GetPluginFactory' in _dll_exports(path)


def _is_clap_dll(path: str) -> bool:
    """CLAP plugins must export clap_entry."""
    if not _HAS_PEFILE:
        return True
    exports = _dll_exports(path)
    if not exports:
        return False
    return 'clap_entry' in exports


# ── depth-limited directory walk ──────────────────────────────────────────────

def _walk(base: str, max_depth: int = 3):
    """os.walk limited to max_depth levels below base."""
    base = os.path.normpath(base)
    for root, dirs, files in os.walk(base):
        depth = root[len(base):].count(os.sep)
        yield root, dirs, files
        if depth >= max_depth:
            dirs.clear()


# ── format scanners ───────────────────────────────────────────────────────────

def _scan_vst2(paths: list, skip: set) -> list:
    plugins = []
    for base in paths:
        if not os.path.isdir(base):
            continue
        try:
            for root, _dirs, files in _walk(base):
                for fname in files:
                    if not fname.lower().endswith('.dll'):
                        continue
                    full = os.path.join(root, fname)
                    if full.lower() in skip:
                        continue
                    if _is_vst2_dll(full):
                        plugins.append(_make_plugin(full, 'VST2'))
        except (PermissionError, OSError):
            pass
    return plugins


def _scan_vst3(paths: list, skip: set) -> list:
    # Walks subfolders too: many installers use VST3\<Vendor>\Plugin.vst3
    plugins = []
    for base in paths:
        if not os.path.isdir(base):
            continue
        try:
            for root, dirs, files in _walk(base):
                # .vst3 bundles are directories — take them, don't recurse in
                for d in [d for d in dirs if d.lower().endswith('.vst3')]:
                    dirs.remove(d)
                    full = os.path.join(root, d)
                    if full.lower() in skip:
                        continue
                    inner = _vst3_inner_dll(full)
                    plugins.append(_make_plugin(full, 'VST3', dll_for_info=inner or None))
                for fname in files:
                    if not fname.lower().endswith('.vst3'):
                        continue
                    full = os.path.join(root, fname)
                    if full.lower() in skip:
                        continue
                    if _is_vst3_dll(full):
                        plugins.append(_make_plugin(full, 'VST3'))
        except (PermissionError, OSError):
            pass
    return plugins


def _scan_clap(paths: list, skip: set) -> list:
    plugins = []
    for base in paths:
        if not os.path.isdir(base):
            continue
        try:
            for root, _dirs, files in _walk(base):
                for fname in files:
                    if not fname.lower().endswith('.clap'):
                        continue
                    full = os.path.join(root, fname)
                    if full.lower() in skip:
                        continue
                    if _is_clap_dll(full):
                        plugins.append(_make_plugin(full, 'CLAP'))
        except (PermissionError, OSError):
            pass
    return plugins


# ── broad Program Files scan ──────────────────────────────────────────────────
#
# Some developers (PianoTeq, Kontakt, etc.) install plugins inside their own
# company folder under Program Files rather than in the standard Common Files
# locations.  This scan walks PF / PF86 looking for any plugin file, skipping
# directories already covered by the targeted scans above.

_BROAD_SCAN_DEPTH = 4   # deep enough for Company/Product/Version/Plugin.vst3

# Paths already handled by the targeted scans — skip them in the broad pass.
_STANDARD_ROOTS = {
    p.lower() for p in [
        os.path.join(PF,   'VSTPlugins'),
        os.path.join(PF,   'Steinberg', 'VSTPlugins'),
        os.path.join(PF,   'Common Files'),
        os.path.join(PF86, 'VSTPlugins'),
        os.path.join(PF86, 'Steinberg', 'VSTPlugins'),
        os.path.join(PF86, 'Common Files'),
    ]
}


def _broad_scan(known: set) -> list:
    """
    Walk Program Files broadly and pick up plugins missed by the targeted scans.
    known: mutable set of lower-case plugin_path strings already discovered.
    """
    plugins = []

    for base in (PF, PF86):
        if not os.path.isdir(base):
            continue
        try:
            for root, dirs, files in _walk(base, max_depth=_BROAD_SCAN_DEPTH):
                root_lower = root.lower()

                # Prune standard roots so we never re-enter them
                dirs[:] = [
                    d for d in dirs
                    if not os.path.join(root_lower, d.lower()) in _STANDARD_ROOTS
                    and not root_lower in _STANDARD_ROOTS
                ]
                if root_lower in _STANDARD_ROOTS:
                    dirs.clear()
                    continue

                # .vst3 bundles appear as directories — handle and don't recurse
                vst3_dirs = [d for d in dirs if d.lower().endswith('.vst3')]
                for d in vst3_dirs:
                    dirs.remove(d)
                    full = os.path.join(root, d)
                    if full.lower() in known:
                        continue
                    known.add(full.lower())
                    inner = _vst3_inner_dll(full)
                    plugins.append(_make_plugin(full, 'VST3', dll_for_info=inner or None))

                # Plugin files
                for fname in files:
                    fl = fname.lower()
                    full = os.path.join(root, fname)
                    if full.lower() in known:
                        continue
                    if fl.endswith('.vst3') and _is_vst3_dll(full):
                        known.add(full.lower())
                        plugins.append(_make_plugin(full, 'VST3'))
                    elif fl.endswith('.dll') and _is_vst2_dll(full):
                        known.add(full.lower())
                        plugins.append(_make_plugin(full, 'VST2'))
                    elif fl.endswith('.clap') and _is_clap_dll(full):
                        known.add(full.lower())
                        plugins.append(_make_plugin(full, 'CLAP'))
        except (PermissionError, OSError):
            pass

    return plugins


def _registry_vst_paths() -> list:
    """Read extra VST2 search paths registered by DAWs / installers."""
    paths = []
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\VST'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\VST'),
        (winreg.HKEY_CURRENT_USER,  r'SOFTWARE\VST'),
    ]
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as k:
                i = 0
                while True:
                    try:
                        _, val, _ = winreg.EnumValue(k, i)
                        if isinstance(val, str) and os.path.isdir(val):
                            paths.append(val)
                        i += 1
                    except OSError:
                        break
        except OSError:
            pass
    return paths


# ── public entry point ────────────────────────────────────────────────────────

def scan_all(progress_callback=None, use_cache=True) -> list:
    """
    Scan all known VST2 / VST3 / CLAP locations.
    When use_cache=True, plugins already recorded in cache.json are returned
    immediately (after confirming they still exist on disk); only new or
    previously-unseen paths are fully processed.
    Returns a deduplicated list of PluginInfo.
    """
    global _inno_by_path
    cb = progress_callback or (lambda msg: None)
    # Folders and installs may have changed since the last scan
    _dir_cache.clear()
    _inno_logs.clear()
    _nearby_cache.clear()
    _inno_by_path = None

    # ── load and validate cache ───────────────────────────────────────────────
    cached: dict = {}
    if use_cache:
        raw = load_cache()
        for path_lower, plugin in raw.items():
            if os.path.exists(plugin.plugin_path):
                cached[path_lower] = plugin
        stale = len(raw) - len(cached)
        if raw:
            parts = [f'{len(cached)} plugin(s) loaded from cache']
            if stale:
                parts.append(f'{stale} removed (no longer found)')
            cb(', '.join(parts) + '. Checking for new plugins…')
        else:
            cb('No cache found. Running full scan…')

    skip = set(cached.keys())   # paths to skip in every scan below

    # ── targeted scans (only unknown paths) ──────────────────────────────────
    reg_extra = _registry_vst_paths()
    vst2_all = list(dict.fromkeys(VST2_PATHS + reg_extra))

    cb('Scanning for new VST2 plugins…')
    new_plugins = _scan_vst2(vst2_all, skip)

    cb('Scanning for new VST3 plugins…')
    new_plugins += _scan_vst3(VST3_PATHS, skip)

    cb('Scanning for new CLAP plugins…')
    new_plugins += _scan_clap(CLAP_PATHS, skip)

    cb('Scanning Program Files for plugins in custom locations…')
    known = skip | {p.plugin_path.lower() for p in new_plugins}
    new_plugins += _broad_scan(known)

    # ── combine, deduplicate, save ────────────────────────────────────────────
    all_plugins = list(cached.values()) + new_plugins

    seen: set = set()
    unique = []
    for p in all_plugins:
        key = p.plugin_path.lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)

    save_cache(unique)

    n_new = len(new_plugins)
    n_cached = len(cached)
    if n_new:
        cb(f'Done — {n_cached} from cache, {n_new} new. {len(unique)} total.')
    else:
        cb(f'Done — {len(unique)} plugin(s) (all from cache, nothing new found).')

    return unique
