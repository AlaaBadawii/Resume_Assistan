'''Confined artifact writes using pinned POSIX directory descriptors.

Paths are normalized lexically, never resolved through destination symlinks.
Every directory below the trusted project root is opened with O_NOFOLLOW.
The handles remain open across processing, and creation/replacement/cleanup
are relative to the pinned destination handle. Platforms without these
operations fail closed rather than falling back to vulnerable path writes.
'''

import os
import secrets
import stat
from pathlib import Path

PRIVATE_COMPONENTS = ('data', 'private', 'processed')


class PrivateOutputError(ValueError):
    '''Controlled, content-free artifact destination error.'''


def normalize_output_path(project_root, path) -> Path:
    '''Return an absolute lexical path confined below the private directory.'''
    try:
        root = Path(os.path.abspath(project_root))
        target = Path(os.path.abspath(path))
        relative = target.relative_to(root)
    except (TypeError, ValueError, OSError):
        raise PrivateOutputError('Outputs must be files under data/private/processed/.') from None
    if (
        relative.parts[:3] != PRIVATE_COMPONENTS
        or len(relative.parts) <= 3
        or '\x00' in str(target)
    ):
        raise PrivateOutputError('Outputs must be files under data/private/processed/.')
    return target


def _require_descriptor_support() -> None:
    operations = (os.open, os.stat, os.mkdir, os.unlink, os.rename)
    if (
        os.name != 'posix'
        or not hasattr(os, 'O_NOFOLLOW')
        or not hasattr(os, 'O_DIRECTORY')
        or not all(operation in os.supports_dir_fd for operation in operations)
        or os.stat not in os.supports_follow_symlinks
    ):
        raise PrivateOutputError(
            'Secure private output requires POSIX no-follow directory-relative operations.'
        )


def _identity(info) -> tuple[int, int]:
    return info.st_dev, info.st_ino


class PrivateOutput:
    '''Pin and validate the directory chain for one artifact destination.

    Opening may create missing parent directories, exclusively relative to
    their already-open parents. create_parents=False is a read-only check;
    an absent suffix is allowed but cannot be written through that handle.
    '''

    def __init__(self, project_root, path, *, create_parents: bool = True) -> None:
        self.root = Path(os.path.abspath(project_root))
        self.path = normalize_output_path(self.root, path)
        self._create_parents = create_parents
        self._fds: list[int] = []
        self._links: list[tuple[int, str, int]] = []
        self._parent_complete = False

    def __enter__(self) -> 'PrivateOutput':
        _require_descriptor_support()
        if self._fds:
            raise PrivateOutputError('Private output directory handle is already open.')
        self._links.clear()
        self._parent_complete = False
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
        try:
            self._fds.append(os.open(self.root, flags))
            components = self.path.relative_to(self.root).parts[:-1]
            for component in components:
                parent_fd = self._fds[-1]
                try:
                    child_fd = os.open(component, flags, dir_fd=parent_fd)
                except FileNotFoundError:
                    if not self._create_parents:
                        self.ensure_current()
                        return self
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=parent_fd)
                    except FileExistsError:
                        # A competing creator must still pass the no-follow open.
                        pass
                    child_fd = os.open(component, flags, dir_fd=parent_fd)
                self._fds.append(child_fd)
                self._links.append((parent_fd, component, child_fd))
            self._parent_complete = True
            self.ensure_current()
            self._validate_target()
            return self
        except OSError:
            self.close()
            raise PrivateOutputError(
                'Private output directories must be accessible real directories, not symlinks.'
            ) from None
        except BaseException:
            self.close()
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        while self._fds:
            os.close(self._fds.pop())

    def ensure_current(self) -> None:
        '''Detect replaced/renamed directory entries without following links.'''
        if not self._fds:
            raise PrivateOutputError('Private output directory handle is not open.')
        try:
            root_info = os.stat(self.root, follow_symlinks=False)
            if not stat.S_ISDIR(root_info.st_mode) or _identity(root_info) != _identity(os.fstat(self._fds[0])):
                raise PrivateOutputError('Private output directory identity changed during processing.')
            for parent_fd, component, child_fd in self._links:
                current = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISDIR(current.st_mode) or _identity(current) != _identity(os.fstat(child_fd)):
                    raise PrivateOutputError('Private output directory identity changed during processing.')
        except OSError:
            raise PrivateOutputError('Private output directory identity changed during processing.') from None

    def _validate_target(self) -> None:
        if not self._parent_complete:
            return
        try:
            info = os.stat(self.path.name, dir_fd=self._fds[-1], follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode):
            raise PrivateOutputError('Private output destination must be a regular file, not a symlink.')

    def write_json(self, payload_json: str) -> None:
        '''Exclusively create and atomically replace relative to the pinned parent.'''
        if not self._parent_complete:
            raise PrivateOutputError('Private output parent directory is not available for writing.')
        self.ensure_current()
        self._validate_target()
        parent_fd = self._fds[-1]
        temporary_name = None
        fd = None
        try:
            for _ in range(32):
                candidate = f'.resume-assistant-{secrets.token_hex(16)}.tmp'
                try:
                    fd = os.open(
                        candidate,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0),
                        0o600,
                        dir_fd=parent_fd,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            if fd is None:
                raise OSError('Could not create an exclusive artifact temporary file.')
            self.ensure_current()
            handle = os.fdopen(fd, 'w', encoding='utf-8')
            fd = None  # ownership transferred to the file object
            with handle:
                handle.write(payload_json)
            self.ensure_current()
            self._validate_target()
            os.replace(
                temporary_name, self.path.name,
                src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
            )
            temporary_name = None
        finally:
            if fd is not None:
                os.close(fd)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass


    def write_bytes(self, payload: bytes) -> None:
        '''Exclusively create and atomically replace binary content.'''
        if not isinstance(payload, bytes):
            raise PrivateOutputError('Private output payload must be bytes.')
        if not self._parent_complete:
            raise PrivateOutputError('Private output parent directory is not available for writing.')
        self.ensure_current()
        self._validate_target()
        parent_fd = self._fds[-1]
        temporary_name = None
        fd = None
        try:
            for _ in range(32):
                candidate = f'.resume-assistant-{secrets.token_hex(16)}.tmp'
                try:
                    fd = os.open(
                        candidate,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0),
                        0o600,
                        dir_fd=parent_fd,
                    )
                except FileExistsError:
                    continue
                temporary_name = candidate
                break
            if fd is None:
                raise OSError('Could not create an exclusive artifact temporary file.')
            self.ensure_current()
            handle = os.fdopen(fd, 'wb')
            fd = None  # ownership transferred to the file object
            with handle:
                handle.write(payload)
            self.ensure_current()
            self._validate_target()
            os.replace(
                temporary_name, self.path.name,
                src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
            )
            temporary_name = None
        finally:
            if fd is not None:
                os.close(fd)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass


def is_private_output(project_root, path) -> bool:
    '''Inspect an existing directory chain without creating directories/files.'''
    try:
        with PrivateOutput(project_root, path, create_parents=False):
            return True
    except (PrivateOutputError, OSError):
        return False
