#!/usr/bin/env python
from socket import gethostname
import collections
import ConfigParser
import getpass
import logging
import optparse
import os
import shutil
import sys
import tempfile
import unittest

APP_NAME = "pbf"

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(APP_NAME)

##################################################
# platform peculariaties, setting the following
# IS_WINDOWS
# LOCAL_USER_ID
# LOCAL_USERNAME
# LOCAL_EMAIL
# DEFAULT_PATH
# DEFAULT_CONFIG
##################################################
IS_WINDOWS = sys.platform.lower().startswith("win")

LOCAL_USER_ID = LOCAL_USERNAME = getpass.getuser()
LOCAL_USERNAME = LOCAL_USERNAME.title()
if IS_WINDOWS:
    try:
        # pylint: disable=F0401
        import win32net, win32api
        USER_INFO_20 = 20
        LOCAL_USERNAME = win32net.NetUserGetInfo(
            win32net.NetGetAnyDCName(),
            win32api.GetUserName(),
            USER_INFO_20,
            )["full_name"] or LOCAL_USERNAME
    except ImportError:
        pass
else:
    # pylint: disable=F0401
    import pwd # only available on non-win32
    LOCAL_USERNAME = pwd.getpwnam(LOCAL_USER_ID).pw_gecos.split(',', 1)[0]
LOCAL_EMAIL = "%s@%s" % (LOCAL_USER_ID, gethostname())

if IS_WINDOWS:
    DEFAULT_PATH = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~/Config/")),
        APP_NAME
        )
else:
    DEFAULT_PATH = os.path.expanduser("~/.config/" + APP_NAME)
DEFAULT_CONFIG = os.path.join(DEFAULT_PATH, "config.ini")

##################################################
# Defaults
##################################################
DEF_PENDING_CATEGORIES = set([
    "bugs",
    "features",
    ])
DEF_DONE = "done",
DEF_PRIORITY = "normal"
DEF_PRIORITIES = set([
    "high",
    DEF_PRIORITY,
    "low",
    ])

##################################################
# configuration .ini constants
##################################################
CONF_SEC_CONFIG = "config"
CONF_EMAIL = "email"
CONF_NAME = "name"
CONF_DIRNAME = "todo"
CONF_PENDING_CATEGORIES = "pending_categories"
CONF_DONE_CATEGORIES = "done_categories"
CONF_DEF_PRIORITY = "default_priority"
CONF_PRIORITIES = "priorities"

##################################################
# parser constants
##################################################
CMD_TEST = "test"
CMD_HELP = "help"
CMD_ADD = "add"
CMD_COMPLETE = "complete"
CMD_DONE1 = "done"
CMD_DONE2 = "do"
CMD_EDIT = "edit"
CMD_COMMENT = "comment"
CMD_LIST = "list"
CMD_SEARCH = "search"
OPT_VERBOSE = "verbose"
OPT_CONFIG = "config"

##################################################
# helper functions
##################################################
def clean_cmd(s):
    return s.strip().lower()

def short_desc(fn):
    return getattr(fn, "__doc__", "<undefined>").splitlines()[0]

##################################################
# implementation
##################################################
def do_test(options, config, args):
    "Run the test suite"
    unittest.main(argv=[__file__])

def do_help(options, config, args):
    "Help on a command"
    log.debug("do_help %r", args)
    if args:
        cmd = clean_cmd(args.pop(0))
        if cmd in CMD_MAP:
            fn = CMD_MAP[cmd]
            print fn.__doc__
            other_names = set(
                name
                for (name, fn_)
                in CMDS
                if fn_ is fn
                and name != cmd
                )
            if other_names:
                print "Aliases:"
                for name in sorted(other_names):
                    print " %s" % name
        else:
            print "No command %r" % cmd
    else:
        print 'Specify "help <CMD>" to learn more about each command'
        func_set = set()
        width = max(len(s) for s, _ in CMDS)
        for (name, fn) in CMDS:
            if id(fn) in func_set: continue
            func_set.add(id(fn))
            print " %-*s  %s" % (
                width,
                name,
                short_desc(fn),
                )
def do_add(options, config, args):
    "Add an item"
    log.debug("do_add %r", args)

def do_complete(options, config, args):
    "List completion options"
    log.debug("do_complete %r", args)

def do_edit(options, config, args):
    "Edit an existing item"
    log.debug("do_edit %r", args)

def do_comment(options, config, args):
    "Comment on an existing item"
    log.debug("do_comment %r", args)

def do_search(options, config, args):
    "List all/matching items"
    log.debug("do_search %r", args)

CMDS = [
    (CMD_HELP, do_help),
    (CMD_ADD, do_add),
    (CMD_COMPLETE, do_complete),
    (CMD_DONE1, do_complete),
    (CMD_DONE2, do_complete),
    (CMD_EDIT, do_edit),
    (CMD_COMMENT, do_comment),
    (CMD_LIST, do_search),
    (CMD_SEARCH, do_search),
    (CMD_TEST, do_test),
    ]
CMD_MAP = dict(CMDS)

def options_cmd_rest(args):
    descriptions = []
    func_set = set()
    for (name, fn) in CMDS:
        if id(fn) in func_set: continue
        func_set.add(id(fn))
        descriptions.append(name)
    description = ', '.join(descriptions)
    parser = optparse.OptionParser(
        usage="%prog <global options> CMD <command options>",
        description="Where CMD is one of [%s]" % description,
        )
    parser.add_option("-v", "--verbose",
        help="Stack multiple times to make more verbose",
        dest=OPT_VERBOSE,
        action="count",
        )
    parser.add_option("-c", "--config",
        help="Specify an alternate config file "
            "instead of %s" % DEFAULT_CONFIG,
        metavar="CONFIG_FILE",
        dest=OPT_CONFIG,
        action="store",
        )
    parser.set_defaults(**{
        OPT_VERBOSE: 1,
        OPT_CONFIG: DEFAULT_CONFIG,
        })
    options, args = parser.parse_args(args)
    if args:
        cmd = clean_cmd(args.pop(0)) or None
    else:
        cmd = None
    return parser, options, cmd, args

def get_default_config():
    try:
        # sort them if we can
        from collections import OrderedDict as config_dict
    except (ImportError, AttributeError), e:
        # otherwise, just default to dict
        config_dict = dict
    c = ConfigParser.RawConfigParser(
        dict_type=config_dict,
        )
    c.add_section(CONF_SEC_CONFIG)
    for name, categories in (
            (CONF_PENDING_CATEGORIES, DEF_PENDING_CATEGORIES),
            (CONF_PRIORITIES, DEF_PRIORITIES),
            ):
        c.set(CONF_SEC_CONFIG, name, ','.join(sorted(categories)))
    for name, value in (
            (CONF_EMAIL, LOCAL_EMAIL),
            (CONF_NAME, LOCAL_USERNAME),
            (CONF_NAME, CONF_DIRNAME),
            (CONF_DEF_PRIORITY, DEF_PRIORITY),
            ):
        c.set(CONF_SEC_CONFIG, name, value)
    return c

class TestCommands(unittest.TestCase):
    def setUp(self):
        # push to a temp directory
        self._old_wd = os.getcwd()
        self._new_wd = tempfile.mkdtemp()
        os.chdir(self._new_wd)
        # grab a default config
        self.config = get_default_config()

    def tearDown(self):
        # pop the temp directory
        os.chdir(self._old_wd)
        shutil.rmtree(self._new_wd)

    #def test_dump_config(self):
        #self.config.write(sys.stdout)
    def test_complete(self):
        "List of subfunction possibilities"
    def test_add(self):
        "Add a task"
    def test_edit(self):
        "Edit a task"
    def close(self):
        "Close a task"
    def test_add_comment(self):
        "Add a comment"

def main(*args):
    parser, options, cmd, rest = options_cmd_rest(list(args[1:]))
    config = get_default_config()
    log.debug("Command %r", cmd)
    if cmd in CMD_MAP:
        CMD_MAP[cmd](options, config, rest)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv))
