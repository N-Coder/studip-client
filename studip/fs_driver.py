import errno
import os
from os import path

from fuse import FuseOSError, Operations, LoggingMixIn

from studip.views import ViewSynchronizer


class FUSEView(ViewSynchronizer, Operations):
    def __init__(self, sync_dir, config, db, view):
        super().__init__(sync_dir, config, db, view)
        self.fs_tree = {}

    def init(self, root):
        for file in self.db.list_files(
                full=True, select_sync_metadata_only=False, select_sync_no=False
        ):
            cache_name = file.id
            if file.version > 0:
                cache_name += "." + str(file.version)
            file.cache_path = path.join(self.files_dir, cache_name)

            folders, name = path.split(path.normpath(self.format_file_path(file)))
            sub_tree = self.fs_tree
            for folder in folders.split("/"):
                sub_tree = sub_tree.setdefault(folder, {})
            sub_tree[name] = file

    def _resolve(self, partial: str):
        while partial.startswith("/"):
            partial = partial[1:]
        sub_tree = self.fs_tree
        for segment in path.normpath(partial).split("/"):
            if segment == ".":
                continue
            if segment not in sub_tree:
                raise OSError(errno.ENOENT,
                              "No such file or directory '{}' from path '{}'. Options would be: {}".format(
                                  segment, partial, ", ".join(sub_tree.keys())
                              ), partial)
            sub_tree = sub_tree[segment]
        return sub_tree

    def access(self, path, mode):
        full_path = self._resolve(path)
        if isinstance(full_path, dict):
            pass
        elif not os.access(full_path.cache_path, mode):
            raise FuseOSError(errno.EACCES)

    def getattr(self, path, fh=None):
        full_path = self._resolve(path)
        if isinstance(full_path, dict):
            st = os.lstat(self.files_dir)
        else:
            st = os.lstat(full_path.cache_path)
        return dict((key, getattr(st, key)) for key in
                    ('st_atime', 'st_ctime', 'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))

    def readdir(self, path, fh):
        full_path = self._resolve(path)
        if isinstance(full_path, dict):
            yield from ['.', '..']
            yield from full_path.keys()
        else:
            raise OSError(errno.ENOTDIR)

    def open(self, path, flags):
        full_path = self._resolve(path)
        if isinstance(full_path, dict):
            raise OSError(errno.EISDIR)
        else:
            return os.open(full_path.cache_path, flags)

    def read(self, path, length, offset, fh):
        os.lseek(fh, offset, os.SEEK_SET)
        return os.read(fh, length)

    def flush(self, path, fh):
        return os.fsync(fh)

    def release(self, path, fh):
        return os.close(fh)

    def fsync(self, path, fdatasync, fh):
        return self.flush(path, fh)
