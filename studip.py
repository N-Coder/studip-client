import requests
from html.parser import HTMLParser
from enum import IntEnum
import urllib.parse as urlparse
import os, sys
from getpass import getpass
from errno import ENOENT
from configparser import ConfigParser
import appdirs

from parsers import *
from database import Database
from util import prompt_choice
from session import Session


class ApplicationExit(BaseException):
    pass


class Application:
    def print_io_error(self, str, source, e):
        sys.stderr.write("Error: {} {}: {}\n".format(str, source,
                e.strerror if e.strerror else type(e).__name__))


    def create_path(self, dir):
        try:
            os.makedirs(dir, exist_ok=True)
        except Exception as e:
            self.print_io_error("Unable to create directory", dir, e)
            raise ApplicationExit()


    def setup_sync_dir(self):
        cache_dir = appdirs.user_cache_dir("studip", "fknorr")
        self.create_path(cache_dir)
        history_file_name = appdirs.user_cache_dir("studip", "fknorr") + "/history"
        history = []
        try:
            with open(history_file_name, "r", encoding="utf-8") as file:
                history = list(filter(None, file.read().splitlines()))
        except Exception:
            pass

        if "sync_dir" in self.command_line:
            self.sync_dir = self.command_line["sync_dir"]
        else:
            if history:
                self.sync_dir = history[0]
                print("Using last sync directory {} ...".format(self.sync_dir))
            else:
                default_dir = os.path.expanduser("~/StudIP")
                self.sync_dir = os.path.expanduser(input("Sync directory [{}]: ".format(#
                        default_dir)))
                if not self.sync_dir:
                    self.sync_dir = default_dir

        while self.sync_dir in history:
            history.remove(self.sync_dir)
        history.insert(0, self.sync_dir)

        try:
            with open(history_file_name, "w", encoding="utf-8") as file:
                file.write("\n".join(history) + "\n")
        except Exception as e:
            self.print_io_error("Unable to write to", history_file_name, e)
            raise ApplicationExit()

        dot_dir = self.sync_dir + "/.studip"
        self.create_path(dot_dir)

        self.config_file_name = dot_dir + "/studip.conf"
        self.db_file_name = dot_dir + "/cache.sqlite"


    def configure(self):
        self.config = ConfigParser()
        self.config["server"] = {
            "studip_base" : "https://studip.uni-passau.de",
            "sso_base" : "https://sso.uni-passau.de"
        }
        self.config["user"] = {}

        try:
            with open(self.config_file_name, "r", encoding="utf-8") as file:
                self.config.read_file(file)
        except Exception as e:
            if not (isinstance(e, IOError) and e.errno == ENOENT):
                self.print_io_error("Unable to read configuration from", config_file_name, e)
                sys.stderr.write("Starting over with a fresh configuration\n")

        user_config = self.config["user"]
        if "user_name" in user_config:
            self.user_name = user_config["user_name"]
        else:
            self.user_name = input("Stud.IP user name: ")

        if "password" in user_config:
            self.password = user_config["password"]
        else:
            self.password = getpass()

        if "save_login" in user_config and user_config["save_login"][0] in "ynu":
            save_login = user_config["save_login"][0]
        else:
            save_login = prompt_choice("Save login? ([Y]es, [n]o, [u]ser name only)", "ynu",
                    default="y")
            user_config["save_login"] = { "y" : "yes", "n" : "no", "u" : "user name only" }[
                    save_login]

        if save_login in "yu":
            user_config["user_name"] = self.user_name
        if save_login == "y":
            user_config["password"] = self.password

        try:
            with open(self.config_file_name, "w", encoding="utf-8") as file:
                self.config.write(file)
        except Exception as e:
            self.print_io_error("Unable to write to", self.config_file_name, e)
            raise ApplicationExit()


    def open_session(self):
        self.session = Session(self.config, self.database, self.user_name, self.password,
                self.sync_dir)


    def open_database(self):
        try:
            self.database = Database(self.db_file_name)
        except Exception as e:
            self.print_io_error("Unable to open database", self.db_file_name, e)
            raise ApplicationExit()


    def update_database(self):
        interrupt = None
        try:
            self.session.update_metadata()
        except KeyboardInterrupt as e:
            interrupt = e

        self.database.commit()

        if interrupt:
            raise interrupt


    def download_files(self):
        self.session.download_files()


    def clear_cache(self):
        try:
            os.remove(self.db_file_name)
        except Exception as e:
            if not (isinstance(e, IOError) and e.errno == ENOENT):
                self.print_io_error("Unable to remove database file", self.db_file_name, e)
                raise ApplicationExit()

        print("Cache cleared.")


    def show_usage(self, out):
        out.write("""Usage: {} <operation> <parameters>

    Possible operations:
        update        Update course database from Stud.IP
        download      Download missing files from known database
        sync          <update>, then <download>
        clear-cache   Clear local course and file database
        help          Show this synopsis
    """.format(sys.argv[0]))


    def parse_command_line(self):
        if len(sys.argv) < 2 or len(sys.argv) > 3:
            return False

        self.command_line = {}

        op = sys.argv[1]
        if op == "help" or op == "--help" or op == "-h":
            op = "help"
        elif op not in [ "update", "download", "sync", "clear-cache" ]:
            return False
        self.command_line["operation"] = op

        if len(sys.argv) >= 3:
            self.command_line["sync_dir"] = sys.argv[2]

        return True


    def main(self):
        if not self.parse_command_line():
            self.show_usage(sys.stderr)
            raise ApplicationExit()

        self.setup_sync_dir()
        self.configure()

        op = self.command_line["operation"]

        if op in [ "update", "download", "sync" ]:
            self.open_database()
            self.open_session()
            if op == "update":
                self.update_database()
            elif op == "download":
                self.download_files()
            elif op == "sync":
                self.update_database()
                self.download_files()
        elif op == "clear-cache":
            self.clear_cache()


if __name__ == "__main__":
    try:
        app = Application()
        app.main()
    except ApplicationExit:
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130) # Standard UNIX exit code for SIGINT
