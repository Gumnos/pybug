#!/usr/bin/env python
from glob import glob
from socket import gethostname
import collections
import ConfigParser
import getpass
import logging
import optparse
import os
import re
import shutil
import sys
import tempfile
import unittest

APP_NAME = "pb"

DEFAULT_ERROR_LEVEL = logging.FATAL
logging.basicConfig(level=DEFAULT_ERROR_LEVEL)
log = logging.getLogger(APP_NAME)

##################################################
# platform peculariaties, setting the following
# IS_WINDOWS
# LOCAL_USER_ID
# LOCAL_USERNAME
# LOCAL_EMAIL
# DEFAULT_USER_DIR
# DEFAULT_USER_CONFIG
# DEFAULT_SYSTEM_DIR
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
LOCAL_EMAIL = (
    os.environ.get("EMAIL", "") or
    "%s@%s" % (LOCAL_USER_ID, gethostname())
    )

if IS_WINDOWS:
    DEFAULT_USER_DIR = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~/Config/")),
        APP_NAME
        )
    DEFAULT_SYSTEM_DIR = os.path.join(
        os.environ.get("PROGRAMFILES", os.path.expanduser("c:/Program Files")),
        APP_NAME
        )
else:
    DEFAULT_USER_DIR = os.path.expanduser("~/.config/" + APP_NAME)
    DEFAULT_SYSTEM_DIR = "/etc"
DEFAULT_USER_CONFIG = os.path.join(DEFAULT_USER_DIR, "config.ini")
DEFAULT_SYSTEM_CONFIG = os.path.join(DEFAULT_SYSTEM_DIR, "%s.ini" % APP_NAME)

##################################################
# Defaults
##################################################
DEF_PENDING_CATEGORIES = set([
    "bugs",
    "features",
    ])
DEF_DONE_CATEGORY = "done",
DEF_DIRNAME = "todo"
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
CONF_USERNAME = "name"
CONF_DIRNAME = "dirname"
CONF_PENDING_CATEGORIES = "pending_categories"
CONF_DONE_CATEGORY = "done_category"
CONF_DEF_PRIORITY = "default_priority"
CONF_PRIORITIES = "priorities"

##################################################
# parser constants
##################################################
CMD_TEST = "test"
CMD_HELP = "help"
CMD_ADD = "add"
CMD_COMPLETE = "complete"
CMD_CLOSE = "close"
CMD_DONE1 = "done"
CMD_DONE2 = "do"
CMD_EDIT = "edit"
CMD_SHOW = "show"
CMD_COMMENT = "comment"
CMD_LIST = "list"
CMD_SEARCH = "search"
OPT_VERBOSE = "verbose"
OPT_CONFIG = "config"

OPT_PRIORITY = "priority"
OPT_EMAIL = "email"
OPT_USERNAME = "username"
OPT_REVISION = "revision"

##################################################
# helper functions
##################################################
def clean_cmd(s):
    return s.strip().lower()

def short_desc(fn):
    return getattr(fn, "__doc__", "<undefined>").splitlines()[0]

def find_dir(dirname, create=False, _result_cache={}):
    """Walks up from the current directory to find 'dirname'
    If 'dirname' can't be found, create such a directory in
    the current directory
    """
    if dirname in _result_cache:
        return _result_cache[dirname]
    cwd = last = loc = os.getcwd()
    while not os.path.isdir(os.path.join(loc, dirname)):
        loc, _ = os.path.split(loc)
        if loc == last:
            # we've walked up to the root
            if create:
                log.info("Creating %s", os.path.join(cwd, dirname))
                os.mkdir(dirname)
                loc = cwd
                break
            else:
                return None
        last = loc
    else:
        if loc != cwd:
            log.info("Walked up to find %s in %s", dirname, loc)
    results = os.path.join(loc, dirname)
    _result_cache[dirname] = results
    return results

def find_dir_based_on_config(config, create=True):
    dirname = config.get(CONF_SEC_CONFIG, CONF_DIRNAME)
    return find_dir(dirname, create=create)

def find_or_create_category_dir(dirname, category):
    """Look in 'dirname' for a directory named 'category'
    If it exists and is not a directory, raise ValueError
    """
    cat_lower = category.strip().lower()
    for name in os.listdir(todo_dir):
        full_name = os.path.join(todo_dir, name)
        if name == category:
            if os.path.isdir(full_name):
                return full_name
            else:
                raise ValueError("Directory %r already exists as file" %
                    category)
    for name in os.listdir(todo_dir):
        full_name = os.path.join(todo_dir, name)
        if name.strip().lower() == cat_lower():
            if os.path.isdir(full_name):
                return full_name
            else:
                raise ValueError("Directory %r already exists as file" %
                    category)
    log.info("Creating %s", os.path.join(dirname, category))
    os.mkdir(os.path.join(dirname, category))

##################################################
# helper class for VCS integration
##################################################
class VCS(object):
    @classmethod
    def is_here(self, dir): return False
    def __init__(self, dir, config):
        self.dir=dir
        self.default_email = config.get(CONF_SEC_CONFIG, CONF_EMAIL)
        self.default_user = config.get(CONF_SEC_CONFIG, CONF_USERNAME)
    def get_name(self): return self.default_user
    def get_email(self): return self.default_email
    def get_rev(self): return None
    def _output_of(self, cmd):
        "a helper function to fetch the output of a given command"
        import subprocess
        return subprocess.check_output(cmd).strip()

class Git(VCS):
    @classmethod
    def is_here(self, dir):
        return bool(find_dir('.git'))
    def get_name(self):
        return self._output_of("git config --get user.name") or self.default_user
    def get_email(self):
        return self._output_of("git config --get user.email") or self.default_email
    def get_rev(self):
        return self._output_of("git rev-parse HEAD")
class CombinedUserEmailVCS(VCS):
    def __init__(self, dir, config):
        VCS.__init__(self, dir, config)
        info = self.get_useremail()
        user_email_re = re.compile('(.*?) +<(.*)$')
        m = user_email_re.match(info)
        if m:
            self.name, self.email = [s.strip() for s in m.groups()]
        else:
            self.name = self.default_user
            self.email = self.default_email
    def get_useremail(self): raise NotImplementedError
    def get_name(self):
        return self.name
    def get_email(self):
        return self.email
    
class Bazaar(CombinedUserEmailVCS):
    @classmethod
    def is_here(self, dir):
        return bool(find_dir('.bzr'))
    def get_useremail(self):
        info = self._output_of("bzr whoami")
    def get_rev(self):
        return self._output_of("bzr version-info --template='{revision_id}' --custom")

class Mercurial(CombinedUserEmailVCS):
    @classmethod
    def is_here(self, dir):
        return bool(find_dir('.hg'))
    def get_useremail(self):
        info = self._output_of("hg showconfig ui.username")
    def get_rev(self):
        return self._output_of("hg parents --template '{node}'")

class Subversion(VCS):
    @classmethod
    def is_here(self, dir):
        # Subversion only checks the current directory
        return os.path.isdir('.svn')
    def get_rev(self):
        pass

VCS_HELPERS = [
    Git,
    Bazaar,
    Mercurial,
    Subversion,
    ]

def find_vcs(dir):
    for vcs in VCS_HELPERS:
        if vcs.is_here(dir):
            return vcs(dir)
    return None

##################################################
# implementation
##################################################
def do_test(options, config, args):
    """Run the test suite
    """
    unittest.main(argv=[__file__])
    return []

def do_help(options, config, args):
    """Help on a command
    """
    results = []
    if args:
        cmd = clean_cmd(args.pop(0))
        if cmd in CMD_MAP:
            fn = CMD_MAP[cmd]
            results.extend(fn.__doc__.splitlines())
            other_names = set(
                name
                for (name, fn_)
                in CMDS
                if fn_ is fn
                and name != cmd
                )
            if other_names:
                results.append("Aliases:")
                results.extend(
                    " %s" % name
                    for name in sorted(other_names)
                    )
        else:
            results.append("No command %r" % cmd)
    else:
        results.append('Specify "help <CMD>" to learn more about each command')
        func_set = set()
        width = max(len(s) for s, _ in CMDS)
        for (name, fn) in CMDS:
            if id(fn) in func_set: continue
            func_set.add(id(fn))
            results.append(" %-*s  %s" % (
                width,
                name,
                short_desc(fn),
                ))
    return results

def do_add(options, config, args):
    """Add an item
    """
    results = []
    parser = tweaking_options(config)
    add_options, add_args = parser.parse_args(args)
    todo_dir = find_dir_based_on_config(config)
    if add_args:
        subject = ' '.join(add_args)
    else:
        subject = "NO SUBJECT"
    import pdb; pdb.set_trace()
    return results

def do_complete(options, config, args):
    """List completion options
    """
    results = []
    return results

def do_edit(options, config, args):
    """Edit an existing item
    """
    results = []
    return results

def do_comment(options, config, args):
    """Comment on an existing item
    """
    results = []
    return results

def do_show(options, config, args):
    """Show a detailed view of items
    """
    results = []
    return results

def iter_todos(config, include_done=True):
    todo_dir = find_dir_based_on_config(config)
    done_dir = config.get(CONF_SEC_CONFIG, CONF_DONE_CATEGORY)
    for name in os.listdir(todo_dir):
        if name == done_dir and not include_done:
            continue
        full_name = os.path.join(todo_dir, name)
        if not os.path.isdir(full_name): continue
        for fname in glob(os.path.join(full_name, "*mbox")):
            yield os.path.join(full_name, fname)

def do_search(options, config, args):
    """List all/matching items
    """
    results = []
    for mbox in iter_todos(config):
        results.append(mbox)
    return results

CMDS = [
    (CMD_HELP, do_help),
    (CMD_ADD, do_add),
    (CMD_COMPLETE, do_complete),
    (CMD_CLOSE, do_complete),
    (CMD_DONE1, do_complete),
    (CMD_DONE2, do_complete),
    (CMD_EDIT, do_edit),
    (CMD_COMMENT, do_comment),
    (CMD_SHOW, do_show),
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
    parser.disable_interspersed_args()
    parser.add_option("-v", "--verbose",
        help="Stack multiple times to make more verbose",
        dest=OPT_VERBOSE,
        action="count",
        )
    parser.add_option("-c", "--config",
        help="Specify an alternate config file "
            "instead of %s" % DEFAULT_USER_CONFIG,
        metavar="CONFIG_FILE",
        dest=OPT_CONFIG,
        action="store",
        )
    parser.set_defaults(**{
        OPT_VERBOSE: 0,
        OPT_CONFIG: DEFAULT_USER_CONFIG,
        })
    options, args = parser.parse_args(args)
    if args:
        cmd = clean_cmd(args.pop(0)) or None
    else:
        cmd = None
    return parser, options, cmd, args

def sloppy_choice_callback(option, opt_str, value, parser, choices):
    "Works like a <choice> option, but allows for partial matching"
    if len(parser.rargs) == 0:
        raise optparse.OptionValueError("Must specify a priority from %r" %
            choices)
    given = parser.rargs.pop(0).strip().lower()
    if given in choices:
        value = given
    else:
        # see if it starts with the given text
        possible = [
            choice
            for choice in choices
            if choice.startswith(given)
            ]
        if not possible:
            # see if it contains the given text
            possible = [
                choice
                for choice in choices
                if given in choice
                ]
            if not possible:
                raise optparse.OptionValueError(
                    "Must specify a priority from %r" %
                    choices)
        if len(possible) > 1:
            raise optparse.OptionValueError("%r is ambiguous, could be %r" %
                (given, possible))
        value = possible[0]
    setattr(parser.values, option.dest, value)

def tweaking_options(config):
    """Adds options for modifying an item
    """
    priority_string = config.get(CONF_SEC_CONFIG, CONF_PRIORITIES)
    priorities = [
        p.strip().lower()
        for p in set(priority_string.split(','))
        ]
    parser = optparse.OptionParser()
    default_email = config.get(CONF_SEC_CONFIG, CONF_EMAIL)
    default_user = config.get(CONF_SEC_CONFIG, CONF_USERNAME)
    default_priority = config.get(CONF_SEC_CONFIG, CONF_DEF_PRIORITY)
    parser.add_option("-e", "--email",
        help="Email adddress of the submitter "
            "(default %r)" % default_email,
        dest=OPT_EMAIL,
        action="store",
        default=default_email,
        )
    parser.add_option("-u", "--user",
        help="Name of the submitter "
            "(default %r)" % default_user,
        dest=OPT_USERNAME,
        action="store",
        default=default_user,
        )
    parser.add_option("-r", "--revision",
        help="Revision number in which this was found",
        dest=OPT_REVISION,
        action="store",
        default=None,
        )
    parser.add_option("-p", "--priority",
        help="One of [%s], (default %r)" % (
            priority_string,
            default_priority,
            ),
        dest=OPT_PRIORITY,
        action="callback",
        callback=sloppy_choice_callback,
        callback_args=(priorities,),
        default=config.get(CONF_SEC_CONFIG, CONF_DEF_PRIORITY),
        )
    return parser

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
            (CONF_USERNAME, LOCAL_USERNAME),
            (CONF_DIRNAME, DEF_DIRNAME),
            (CONF_DONE_CATEGORY, DEF_DONE_CATEGORY),
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
        main(CMD_ADD, "join", "together")
    def test_edit(self):
        "Edit a task"
    def close(self):
        "Close a task"
    def test_add_comment(self):
        "Add a comment"

def increase_verbosity(cur_level, levels):
    known = sorted((
        v
        for v in logging._levelNames
        if not isinstance(v, basestring)
        and v != 0
        and v < cur_level
        ), reverse=True
        )
    if levels >= len(known):
        levels = len(known) - 1
    return known[levels]

def main(*args):
    parser, options, cmd, rest = options_cmd_rest(list(args[1:]))
    new_verbosity = increase_verbosity(
        DEFAULT_ERROR_LEVEL,
        getattr(options, OPT_VERBOSE)
        )
    log.setLevel(new_verbosity)
    log.info("New logging level: %s", logging.getLevelName(new_verbosity))
    config = get_default_config()
    config.read([
        os.path.join(DEFAULT_SYSTEM_CONFIG),
        options.config,
        ])
    if cmd is None:
        parser.print_help()
        return 1
    if cmd not in CMD_MAP:
        rest = [cmd] + rest
        cmd = CMD_LIST
    log.debug("Command %r %r", cmd, rest)
    results = CMD_MAP[cmd](options, config, rest)
    for result in results:
        print result

if __name__ == "__main__":
    sys.exit(main(*sys.argv))
