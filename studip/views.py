import os,time, re

from os import path

from .util import ellipsize, escape_file_name, lexicalise_semester


class ViewSynchronizer:
    def __init__(self, sync_dir, config, db, view):
        super().__init__()

        self.sync_dir = sync_dir
        self.config = config
        self.db = db
        self.view = view
        self.meta_dir = path.join(self.sync_dir, ".studip")
        self.files_dir = path.join(self.meta_dir, "files")
        self.view_dir = path.join(self.sync_dir, self.view.base if self.view.base else "")

        # Find all known files that have been fetched into .studip/files
        fetched_files = []
        for file in self.db.list_files(full=True, select_sync_metadata_only=False,
                select_sync_no=False):
            file_name = file.id
            if file.version > 0:
                file_name += "." + str(file.version)
            abs_path = path.join(self.files_dir, file_name)
            if path.isfile(abs_path):
                file.inode = os.lstat(abs_path).st_ino
                fetched_files.append(file)

        # Find all files hardlinked to a fetched file within the view's directory, tree
        self.existing_files = []
        for cwd, dirs, files in os.walk(self.view_dir):
            if cwd.startswith(self.meta_dir): continue

            for f in files:
                abs_path = os.path.join(cwd, f)
                inode = os.lstat(abs_path).st_ino
                existing = next((f for f in fetched_files if f.inode == inode), None)
                if existing:
                    self.existing_files.append(existing)

        # From the checkouts db table, derive which files have been deleted and which
        # should be checked out
        self.new_files = []
        self.deleted_files = []

        checked_out_files = self.db.list_checkouts(view.id)
        for f in fetched_files:
            # File is known, but not checked out
            if not f in self.existing_files:
                if f.id in checked_out_files:
                    self.deleted_files.append(f)
                else:
                    self.new_files.append(f)
            # File is checked out, but we don't have a record of it (e.g. after reset-deleted)
            elif f.id not in checked_out_files:
                self.db.add_checkout(self.view.id, f.id)

        self.db.commit()

    def __escape_file(self, str):
        return escape_file_name(str, self.view.charset, self.view.escape)

    def __escape_path(self, folders):
        return path.join(*map(self.__escape_file, folders)) if folders else ""

    def checkout(self):
        if not self.view:
            raise SessionError("View does not exist")

        modified_folders = set()
        copyrighted_files = []

        try:
            pending_files = []
            for file in self.new_files:
                rel_path = self.format_file_path(file)
                abs_path = path.join(self.view_dir, rel_path)

                # First update modified_folders, then create directories.
                folder = path.dirname(rel_path)
                while folder:
                    modified_folders.add(folder)
                    folder = path.dirname(folder)

                if not path.isfile(abs_path):
                    pending_files.append((file, rel_path, abs_path))

            first_file = True
            for i, (file, rel_path, abs_path) in enumerate(pending_files):
                if first_file:
                    print()
                    first_file = False
                print("Checking out file {}/{}: {}...".format(i+1, len(pending_files),
                        ellipsize(file.description, 50)))

                if file.copyrighted:
                    copyrighted_files.append(rel_path)

                file_name = file.id
                if file.version > 0:
                    file_name += "." + str(file.version)
                os.makedirs(path.dirname(abs_path), exist_ok=True)
                os.link(path.join(self.files_dir, file_name), abs_path)
                self.db.add_checkout(self.view.id, file.id)

        finally:
            self.db.commit()

            modified_folders = list(modified_folders)
            modified_folders.sort(key=lambda f: len(f), reverse=True)

            def update_directory_mtime(dir):
                latest_ctime = 0
                for file in os.listdir(dir):
                    if not file.startswith("."):
                        latest_ctime = max(latest_ctime, path.getmtime(dir + "/" + file))

                # This may fail if a directory has not been created yet.
                try:
                    os.utime(dir, (latest_ctime, latest_ctime))
                except Exception:
                    pass

            for folder in modified_folders:
                update_directory_mtime(path.join(self.view_dir, folder))
            if self.view.base:
                update_directory_mtime(self.view_dir)
            update_directory_mtime(self.sync_dir)

            if copyrighted_files:
                print("\n" + "-"*80)
                print("The following files have special copyright notices:\n")
                for file in copyrighted_files:
                    print("  -", file)
                print("\nPlease make sure you have looked up, read and understood the terms and"
                        " conditions of these files before proceeding to use them.")
                print("-"*80 + "\n")

        # Create course folders for all courses that do not have files yet
        for course in self.db.list_courses(full=True, select_sync_metadata_only=False,
                                           select_sync_no=False):
            rel_path = self.format_course_path(course)
            abs_path = path.join(self.view_dir, rel_path)

            try:
                os.makedirs(path.dirname(abs_path), exist_ok=False)
                print("Created folder for empty {} {}".format(course.type, course.name))
            except OSError:  # Folder already exists
                pass

    def format_file_path(self, file):
        descr_no_ext = file.description
        if descr_no_ext.endswith("." + file.extension):
            descr_no_ext = descr_no_ext[:-1 - len(file.extension)]

        short_path = file.path
        if short_path[0] == "Allgemeiner Dateiordner":
            short_path = short_path[1:]

        extension = ("." + file.extension) if file.extension else ""
        if file.version > 0:
            extension = self.__escape_file(" (StudIP Version {})".format(file.version + 1)) + extension

        tokens = {
            "semester": self.__escape_file(file.course_semester),
            "semester-lexical": self.__escape_file(lexicalise_semester(file.course_semester)),
            "semester-lexical-short": self.__escape_file(lexicalise_semester(file.course_semester, short=True)),
            "course-id": file.course,
            "course-abbrev": self.__escape_file(file.course_abbrev),
            "course": self.__escape_file(file.course_name),
            "type": self.__escape_file(file.course_type),
            "type-abbrev": self.__escape_file(file.course_type_abbrev),
            "path": self.__escape_path(file.path),
            "short-path": self.__escape_path(short_path),
            "id": file.id,
            "name": self.__escape_file(file.name),
            "ext": extension,
            "description": self.__escape_file(file.description),
            "descr-no-ext": self.__escape_file(descr_no_ext),
            "author": self.__escape_file(file.author),
            "time": self.__escape_file(str(file.local_date))
        }
        return self.view.format.format(**tokens)

    def format_course_path(self, course):
        # Construct a dummy file for extracting the fromatted path
        tokens = {
            "semester": self.__escape_file(course.semester),
            "semester-lexical": self.__escape_file(lexicalise_semester(course.semester)),
            "semester-lexical-short": self.__escape_file(lexicalise_semester(course.semester, short=True)),
            "course-id": course.id,
            "course": self.__escape_file(course.name),
            "course-abbrev": self.__escape_file(course.abbrev),
            "type": self.__escape_file(course.type),
            "type-abbrev": self.__escape_file(course.type_abbrev),
            "path": "",
            "short-path": "",
            "id": "0" * 32,
            "name": "dummy",
            "ext": "txt",
            "description": "dummy.txt",
            "descr-no-ext": "dummy",
            "author": "A",
            "time": self.__escape_file("0000-00-00 00:00:00"),
        }
        return self.view.format.format(**tokens)

    def remove(self):
        if not self.view:
            raise SessionError("View does not exist")

        # Remove our files, mark directories containing foreign files
        directories = []
        directories_to_keep = []
        for cwd, dirs, files in os.walk(self.view_dir):
            if cwd.startswith(self.meta_dir): continue

            has_foreign_files = False
            for lf in files:
                # Is this file a hardlink to a file we control?
                abs_path = os.path.join(cwd, lf)
                inode = os.lstat(abs_path).st_ino
                if any(f.inode == inode for f in self.existing_files):
                    os.unlink(abs_path)
                else:
                    has_foreign_files = True

            directories += [path.join(cwd, d) for d in dirs if d != ".studip"]
            if has_foreign_files:
                directories_to_keep.append(cwd)

        # Sort descending by length so that subdirectories appear before their parents
        directories.sort(key=len, reverse=True)

        # Remove empty directories
        for dir in directories:
            if not any (d.startswith(dir) for d in directories_to_keep):
                os.rmdir(dir)

        if directories_to_keep:
            print("The following directories contain unmanaged files and were kept:\n  - "
                    + "\n  - ".join(directories_to_keep))
        elif self.view_dir in directories and self.view.base: # Do not remove root dir
            os.rmdir(self.view_dir)

        self.view = None


    def reset_deleted(self):
        if not self.view:
            raise SessionError("View does not exist")

        self.db.reset_checkouts(self.view.id)
        self.db.commit()

