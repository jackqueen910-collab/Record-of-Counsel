"""Publish a complete, immutable export generation through one atomic pointer.

Old flat output folders remain readable. Failed builds and previous generations
are retained for recovery; readers never combine files from different builds.
"""
from pathlib import Path
import re
import uuid

from .common import RocError, read_json, write_json
from .output import export_local


def output_directory(run_folder):
    folder = Path(run_folder) / 'output'
    pointer = folder / 'current.json'
    if not pointer.exists():
        return folder  # Pre-generation runs.
    generation = read_json(pointer).get('generation')
    if not isinstance(generation, str) or not re.fullmatch(r'[0-9a-f]{32}', generation):
        raise RocError('Invalid export generation. Restore its saved manifest before downloading.')
    path = folder / 'generations' / generation
    if not (path / 'result.json').is_file():
        raise RocError('Published export generation is missing. Restore it before downloading.')
    return path


def export_metadata(run_folder, directory=None):
    directory = output_directory(run_folder) if directory is None else directory
    path = directory / 'result.json' if directory.name != 'output' else Path(run_folder) / 'result.json'
    return read_json(path) if path.exists() else {}


def incomplete_export(run_folder):
    folder = Path(run_folder) / 'output'
    build = folder / 'building.json'
    if not build.exists():
        return False
    current = folder / 'current.json'
    return not current.exists() or read_json(build)['generation'] != read_json(current)['generation']


def publish_exports(cases, run_folder, title, metadata):
    folder = Path(run_folder) / 'output'
    generation = uuid.uuid4().hex
    directory = folder / 'generations' / generation
    write_json(folder / 'building.json', {'generation': generation})
    path = export_local(cases, directory, title, metadata)
    write_json(directory / 'result.json', metadata | {'workbook': str(path)})
    # All data and metadata are complete before readers can discover this build.
    write_json(folder / 'current.json', {'generation': generation})
    return path
