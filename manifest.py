"""
Manifest schema for .pgz backup files.

The manifest.json stored inside every .pgz describes where each plugin lives
in the archive and where it should be restored on the target machine.
"""

import json
import datetime
from dataclasses import dataclass, field

MANIFEST_VERSION = 1
APP_NAME = 'Plugin Vault'


@dataclass
class DataArchive:
    archive_path: str   # relative path inside the zip  (e.g. "data/0_1/")
    dest_path: str      # absolute destination parent dir on restore


@dataclass
class PluginEntry:
    name: str
    company: str
    description: str
    plugin_type: str            # 'VST2', 'VST3', 'CLAP'
    plugin_archive_path: str    # relative prefix inside zip (e.g. "plugins/0/")
    plugin_dest_path: str       # absolute destination parent dir on restore
    data_archives: list = field(default_factory=list)   # list[DataArchive]


@dataclass
class Manifest:
    version: int = MANIFEST_VERSION
    app: str = APP_NAME
    created: str = ''
    plugins: list = field(default_factory=list)   # list[PluginEntry]

    def __post_init__(self):
        if not self.created:
            self.created = datetime.datetime.now().isoformat()

    def to_json(self) -> str:
        d = {
            'version': self.version,
            'app': self.app,
            'created': self.created,
            'plugins': [
                {
                    'name': p.name,
                    'company': p.company,
                    'description': p.description,
                    'type': p.plugin_type,
                    'plugin_archive_path': p.plugin_archive_path,
                    'plugin_dest_path': p.plugin_dest_path,
                    'data_archives': [
                        {'archive_path': da.archive_path, 'dest_path': da.dest_path}
                        for da in p.data_archives
                    ],
                }
                for p in self.plugins
            ],
        }
        return json.dumps(d, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> 'Manifest':
        d = json.loads(text)
        m = cls(
            version=d.get('version', MANIFEST_VERSION),
            app=d.get('app', APP_NAME),
            created=d.get('created', ''),
        )
        for pd in d.get('plugins', []):
            m.plugins.append(PluginEntry(
                name=pd.get('name', ''),
                company=pd.get('company', ''),
                description=pd.get('description', ''),
                plugin_type=pd.get('type', ''),
                plugin_archive_path=pd.get('plugin_archive_path', ''),
                plugin_dest_path=pd.get('plugin_dest_path', ''),
                data_archives=[
                    DataArchive(
                        archive_path=da.get('archive_path', ''),
                        dest_path=da.get('dest_path', ''),
                    )
                    for da in pd.get('data_archives', [])
                ],
            ))
        return m
