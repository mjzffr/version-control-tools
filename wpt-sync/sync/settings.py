import argparse
import os
from collections import defaultdict
from ConfigParser import RawConfigParser

import model

_config = None

root = os.path.abspath(
    os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),
            os.pardir)))


def read_ini(path):
    print "Loading config from path %s" % path
    parser = RawConfigParser()
    # make option names case sensitive
    parser.optionxform = str
    loaded = parser.read(path)
    if not path in loaded:
        raise ValueError("Failed to load ini file %s" % path)
    return parser


def load(sync_path=None, credentials_path=None, *args, **kwargs):
    global _config
    if _config is None:
        if sync_path is None:
            sync_path = os.path.join(root, "sync.ini")
        if credentials_path is None:
            credentials_path = os.path.join(root, "credentials.ini")
        ini_sync = read_ini(sync_path)
        ini_credentials = read_ini(credentials_path)

        _config = load_files(ini_sync, ini_credentials)
    return _config


def load_files(ini_sync, ini_credentials):
    nested = lambda: defaultdict(nested)

    config = nested()
    config["root"] = root

    for section in ini_sync.sections():
        for name, value in ini_sync.items(section):
            set_value(config, section, name, value, ini_credentials)
    return config


def configure(f):

    def inner(*args, **kwargs):
        config = load(*args, **kwargs)
        return f(config, *args, **kwargs)

    inner.__name__ = f.__name__
    inner.__doc__ = f.__doc__

    return inner


def set_value(config, section, name, value, ini_credentials):
    target = config[section]

    parts = name.split(".")
    for part in parts[:-1]:
        target = target[part]

    if value == "%SECRET%":
        value = ini_credentials.get(section, name)

    if "%ROOT%" in value:
        value = value.replace("%ROOT%", config["root"])

    if value.startswith("$"):
        value = os.environ.get(value[1:])
    elif value.lower() == "true":
        value = True
    elif value.lower() == "false":
        value = False
    else:
        try:
            value = int(value)
        except ValueError:
            pass
    target[parts[-1]] = value

def main():
    root = os.getcwd()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sync", action="store", dest="sync_path",
        help="relative path to ini file for all sync settings")
    parser.add_argument(
        "--credentials", action="store", dest="credentials_path"
        help="relative path to ini file for credential settings")
    args = parser.parse_args()
    config = load(**vars(args))
    print config
    model.configure(config)
    model.create()
    return config

if __name__ == "__main__":
    main()
