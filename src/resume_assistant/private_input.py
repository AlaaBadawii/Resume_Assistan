'''Local artifact reads through pinned, no-follow POSIX directory handles.'''

import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


class PrivateInputError(ValueError):
    '''Controlled artifact input error, containing no path or artifact content.'''


class PrivateInputMissingError(PrivateInputError):
    '''Raised when a pinned local input does not exist.'''


@dataclass(frozen=True, repr=False)
class CapturedSource:
    '''One immutable document input; private bytes are never included in repr.'''

    filename: str
    data: bytes
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'sha256', hashlib.sha256(self.data).hexdigest())


def _file_signature(info) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _check_directories(links, label) -> None:
    for parent_fd, component, child_fd in links:
        current = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        pinned = os.fstat(child_fd)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
            raise PrivateInputError(f'{label} directory identity changed during reading.')


def _read_local_bytes(path, *, label: str, missing_error=PrivateInputError) -> tuple[str, bytes]:
    '''Capture an unchanged regular file using pinned, no-follow ancestry.'''
    try:
        raw_path = os.fspath(path)
        if (
            not isinstance(raw_path, str) or not raw_path.strip() or '\x00' in raw_path
            or '://' in raw_path or '..' in Path(raw_path).parts
        ):
            raise PrivateInputError(f'{label} must be a local path without traversal components.')
        absolute = Path(os.path.abspath(raw_path))
    except PrivateInputError:
        raise
    except (TypeError, ValueError, OSError):
        raise PrivateInputError(f'{label} must be a readable local file path.') from None
    if (
        os.name != 'posix' or not hasattr(os, 'O_DIRECTORY') or not hasattr(os, 'O_NOFOLLOW')
        or os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
    ):
        raise PrivateInputError(f'{label} requires POSIX no-follow directory-relative reads.')

    directories, links = [], []
    file_fd = None
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
    try:
        directories.append(os.open(absolute.anchor, directory_flags))
        for component in absolute.parts[1:-1]:
            parent_fd = directories[-1]
            child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            directories.append(child_fd)
            links.append((parent_fd, component, child_fd))
        _check_directories(links, label)
        parent_fd = directories[-1]
        before = os.stat(absolute.name or '.', dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise PrivateInputError(f'{label} must be a regular file, not a symlink.')
        file_fd = os.open(
            absolute.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_CLOEXEC', 0),
            dir_fd=parent_fd,
        )
        handle = os.fdopen(file_fd, 'rb')
        file_fd = None
        with handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _file_signature(opened) != _file_signature(before):
                raise PrivateInputError(f'{label} file identity changed before reading.')
            data = handle.read()
            after = os.fstat(handle.fileno())
            current = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
            if _file_signature(opened) != _file_signature(after) or _file_signature(after) != _file_signature(current):
                raise PrivateInputError(f'{label} file identity or contents changed during reading.')
        _check_directories(links, label)
        return absolute.name, data
    except FileNotFoundError:
        raise missing_error(f'{label} file could not be found.') from None
    except OSError:
        raise PrivateInputError(f'{label} must use accessible real directories and a readable file, not symlinks.') from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        while directories:
            os.close(directories.pop())


def capture_docx_source(path) -> CapturedSource:
    '''Read document bytes once; filename and digest describe that same capture.'''
    filename, data = _read_local_bytes(
        path, label='Master Resume source input', missing_error=FileNotFoundError,
    )
    return CapturedSource(filename=filename, data=data)


def read_artifact_text(
    path, *, artifact: Literal[
        'job_description', 'job_analysis', 'candidate_evidence', 'candidate_job_match', 'section_plan'
    ]
) -> str:
    '''Read a regular UTF-8 artifact without following any path symlinks.'''
    label = {
        'job_description': 'Saved job description',
        'job_analysis': 'Job analysis artifact input',
        'candidate_evidence': 'Candidate evidence artifact input',
        'candidate_job_match': 'Candidate job match artifact input',
        'section_plan': 'Resume section plan artifact input',
    }[artifact]
    _, data = _read_local_bytes(path, label=label, missing_error=PrivateInputMissingError)
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        raise PrivateInputError(f'{label} file is not valid UTF-8 text.') from None
