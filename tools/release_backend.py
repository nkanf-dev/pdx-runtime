"""Minimal dependency-free PEP 517 wheel backend for already-built runtime DLLs.

Use `python -m build --wheel` after the native build, or release_builder.py.
No sdist/editable/native compiler is advertised by this backend.
"""
from pathlib import Path
from release_builder import collect, metadata, wheel_bytes, TAG


def get_requires_for_build_wheel(config_settings=None):
    return []


def _inputs(config_settings):
    config = config_settings or {}
    result = {}
    supported = ("sdk-source", "native-dll", "skills-source")
    unknown = set(config).difference(supported)
    if unknown:
        raise ValueError("unsupported build settings: " + ", ".join(sorted(unknown)))
    for name in supported:
        value = config.get(name)
        if isinstance(value, list):
            if len(value) != 1:
                raise ValueError("supply one " + name)
            value = value[0]
        if value is not None:
            result[name.replace("-", "_")] = value
    return result


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    project, name, version, files = collect(Path.cwd(), **_inputs(config_settings))
    if metadata_directory is not None:
        info, prepared = metadata(project, name, version, files)
        folder = Path(metadata_directory)
        if folder.name != info:
            folder = folder / info
        for path, raw in prepared.items():
            relative = Path(path).relative_to(info)
            source = folder / relative
            if not source.is_file() or source.read_bytes() != raw:
                raise ValueError("prepared metadata differs from selected payload: " + relative.as_posix())
    filename = name + "-" + version + "-" + TAG + ".whl"
    output = Path(wheel_directory)
    output.mkdir(parents=True, exist_ok=True)
    (output / filename).write_bytes(wheel_bytes(project, name, version, files))
    return filename


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    project, name, version, payload = collect(Path.cwd(), **_inputs(config_settings))
    info, files = metadata(project, name, version, payload)
    output = Path(metadata_directory)
    for path, raw in files.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return info
