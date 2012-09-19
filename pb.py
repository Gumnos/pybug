#!/usr/bin/env python
from glob import glob
from socket import gethostname
from uuid import uuid4 as uuid
import collections
import ConfigParser
import email
import getpass
import hashlib
import logging
import mailbox
import optparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

APP_NAME = "pb"

DEFAULT_ERROR_LEVEL = logging.FATAL
logging.basicConfig(level=DEFAULT_ERROR_LEVEL)
log = logging.getLogger(APP_NAME)

##################################################
# platform peculariaties
#  setting the following:
#  IS_WINDOWS
#  LOCAL_USER_ID
#  LOCAL_USERNAME
#  LOCAL_EMAIL
#  DEFAULT_USER_DIR
#  DEFAULT_USER_CONFIG
#  DEFAULT_SYSTEM_DIR
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
# defaults
##################################################
DEF_PENDING_CATEGORIES = set([
    "bugs",
    "features",
    ])
DEF_DONE_CATEGORY = "done"
DEF_DIRNAME = "todo"
DEF_PRIORITY_STR = "normal"
DEF_PRIORITY_NUM = 3
PRIORITIES = [
    ("highest", 1),
    ("high", 2),
    (DEF_PRIORITY_STR, DEF_PRIORITY_NUM),
    ("low", 4),
    ("lowest", 5),
    ]
PRIORITY_NAME_TO_NUMBER = dict(PRIORITIES)
PRIORITY_NUMBER_TO_NAME = dict(
    (number, name)
    for name, number in PRIORITIES
    )

PRIORITY_STRING = ', '.join(
    "%s/%i" % (name, number)
    for name, number in PRIORITIES
    )

CONTENT_PREFIX_TO_IGNORE = "#" + APP_NAME

##################################################
# configuration .ini constants
##################################################
CONF_SEC_CONFIG = "config"
CONF_DIRNAME = "dirname"
CONF_DONE_CATEGORY = "done_category"
CONF_EMAIL = "email"
CONF_PENDING_CATEGORIES = "pending_categories"
CONF_USERNAME = "name"
CONF_EDITOR = "editor"

##################################################
# parser constants
##################################################
OPT_CONFIG = "config"
OPT_EMAIL = "email"
OPT_PRIORITY = "priority"
OPT_GATHER_MESSAGE = "gather_message"
OPT_REVISION = "revision"
OPT_USERNAME = "username"
OPT_VERBOSE = "verbose"

##################################################
# parser commands
##################################################
CMD_HELP = "help"
CMD_ADD = "add"
CMD_CLOSE = "close"
CMD_DONE1 = "done"
CMD_DONE2 = "do"
CMD_EDIT = "edit"
CMD_SHOW = "show"
CMD_COMMENT = "comment"
CMD_LIST = "list"
CMD_SEARCH = "search"

##################################################
# message-header constants
##################################################
HEAD_SUBJECT = "Subject"
HEAD_FROM = "From"
HEAD_DATE = "Date"
HEAD_REFERENCES = "References"
HEAD_MSG_ID = "Message-ID"
HEAD_PRIORITY = "X-Priority"
HEAD_REVISION = "X-Revision"

##################################################
# helper functions
##################################################
def clean(s):
    return s.strip().lower()

def make_message(existing=None, comments=None):
    """Make a message string, including existing content
    and additional comment material
    """
    TEMPLATE = "%s %%s" % CONTENT_PREFIX_TO_IGNORE
    if existing is None:
        results = [""] * 2 # some blank lines
    else:
        if isinstance(existing, basestring):
            existing = existing.splitlines()
    if comments is None:
        comments = []
    else:
        if isinstance(comments, basestring):
            comments = comments.splitlines()
    # at this point, both 'existing' and 'comments' are lists
    comments.extend([
        "Place more detailed content in here",
        "Any lines beginning with %s will be ignored" % CONTENT_PREFIX_TO_IGNORE,
        ])

    results.extend(
        TEMPLATE % s
        for s in comments
        )
    return '\n'.join(results)

def clean_message(content):
    # remove the comment-prefixed lines
    content = '\n'.join(
        line
        for line in content.splitlines()
        if not line.startswith(CONTENT_PREFIX_TO_IGNORE)
        )
    # remove leading/trailing blank lines
    content = content.strip()
    return content

def edit(editor, s=""):
    "Spawn $editor to edit the content of 's'"
    if sys.stdin.isatty():
        (fd, name) = tempfile.mkstemp(
            prefix=APP_NAME,
            suffix=".txt",
            text=True,
            )
        try:
            f = os.fdopen(fd, "w")
            try:
                f.write(s)
            finally:
                f.close()
            subprocess.call([editor, name])
            f = file(name)
            try:
                return f.read()
            finally:
                f.close()
        finally:
            os.unlink(name)
    else:
        return sys.stdin.read()

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

def find_or_create_category_dir(todo_dir, category):
    """Look in 'todo_dir' for a directory named 'category'
    If it exists and is not a directory, raise ValueError
    """
    cat_lower = clean(category)
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
        if clean(name) == cat_lower:
            if os.path.isdir(full_name):
                return full_name
            else:
                raise ValueError("Directory %r already exists as file" %
                    category)
    log.info("Creating %s", os.path.join(todo_dir, category))
    dest = os.path.join(todo_dir, category)
    os.mkdir(dest)
    return dest

def transform_subject_to_filename(subject, suffix_length=10):
    subj = "".join(
        s.isupper() and s or s.title()
        for s
        in re.findall(r"\w+", subject)
        )
    u = uuid().hex
    unique = hashlib.sha1(subject + u).hexdigest()
    if subj:
        return subj + "-" + unique[:suffix_length]
    else:
        return unique

def get_input(prompt):
    # a hook for possibly using readline
    return raw_input("%s: " % prompt)

def choose(prompt, choices):
    #TODO a hook for menuing and possibly using readline
    log.debug("Arbitrarily choosing %r from %r", choices[0], choices)
    return choices[0]

def make_priority_string(s, default=False):
    s = clean(s)
    if s in PRIORITY_NAME_TO_NUMBER:
        name = s
        number = PRIORITY_NAME_TO_NUMBER[s]
    else:
        try:
            number = int(s)
        except ValueError:
            if default:
                name, number = DEF_PRIORITY_STR, DEF_PRIORITY_NUM
            else:
                raise InvalidPriority("s")
        else:
            if number in PRIORITY_NUMBER_TO_NAME:
                name = PRIORITY_NUMBER_TO_NAME[number]
            else:
                name, number = DEF_PRIORITY_STR, DEF_PRIORITY_NUM
    return "%s (%s)" % (number, name.title())

def build_item(
        username,
        email_address,
        subject,
        category,
        priority,
        revision,
        content,
        ):
    msg = email.MIMEText.MIMEText(content)
    user_string = email.utils.formataddr((username, email_address))
    msg[HEAD_FROM] = user_string
    msg[HEAD_SUBJECT] = subject
    msg[HEAD_DATE] = email.utils.formatdate()
    msg[HEAD_MSG_ID] = email.utils.make_msgid(
        transform_subject_to_filename(subject))
    msg[HEAD_PRIORITY] = make_priority_string(priority)
    if revision:
        msg[HEAD_REVISION] = revision
    msg.preamble = content
    msg = mailbox.mboxMessage(msg)
    msg.set_from(user_string)
    msg.set_unixfrom(user_string)
    return msg

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
def do_help(options, config, args):
    """Help on a command
    """
    results = []
    if args:
        cmd = clean(args.pop(0))
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
    categories_str = config.get(CONF_SEC_CONFIG, CONF_PENDING_CATEGORIES)
    categories = set(
        clean(category)
        for category
        in categories_str.split(',')
        )
    done_category = clean(config.get(CONF_SEC_CONFIG, CONF_DONE_CATEGORY))
    categories.add(done_category)
    if add_args:
        category = clean(add_args[0])
        category = guess_one_of(category, sorted(categories), "Category")
        if category is None:
            # TODO
            pass
        else:
            del add_args[0]
    else:
        category = choose("Category", sorted(categories))
    log.info("Category %r", category)

    subject = ''
    while not subject:
        if add_args:
            subject = ' '.join(arg.strip() for arg in add_args)
        else:
            subject = get_input("Summary").strip()
    log.info("Subject %r", subject)

    if getattr(add_options, OPT_GATHER_MESSAGE):
        orig_content = make_message(comments=subject.encode("string_escape"))
        content = edit(getattr(add_options, CONF_EDITOR), orig_content)
        content = clean_message(content)
    else:
        content = subject + '\n'

    todo_dir = find_dir_based_on_config(config)
    dest_dir = find_or_create_category_dir(todo_dir, category)
    fname = transform_subject_to_filename(subject) + ".mbox"
    item = build_item(
        add_options.username,
        add_options.email,
        subject,
        category,
        clean(getattr(add_options, OPT_PRIORITY)),
        getattr(add_options, OPT_REVISION),
        content,
        )
    full_fname = os.path.join(dest_dir, fname)
    log.debug("Location %r", full_fname)
    mbox = mailbox.mbox(full_fname)
    results.append("Added " + os.path.relpath(full_fname))
    mbox.lock()
    try:
        mbox.add(item)
    finally:
        mbox.unlock()
    return results

def do_close(options, config, args):
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
    (CMD_CLOSE, do_close),
    (CMD_DONE1, do_close),
    (CMD_DONE2, do_close),
    (CMD_EDIT, do_edit),
    (CMD_COMMENT, do_comment),
    (CMD_SHOW, do_show),
    (CMD_LIST, do_search),
    (CMD_SEARCH, do_search),
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
        cmd = clean(args.pop(0)) or None
    else:
        cmd = None
    return parser, options, cmd, args

def guess_one_of(given, choices, prompt=False):
    if given in choices: return given
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
        if not possible: return None
    if len(possible) > 1:
        if prompt:
            return choose("Category", sorted(possible))
    return possible[0]

def sloppy_choice_callback(option, opt_str, given, parser, choices):
    "Works like a <choice> option, but allows for partial matching"
    WARNING = ("Must specify a priority from [%s]" %
            ", ".join(choices))
    value = guess_one_of(given, choices)
    setattr(parser.values, option.dest, value)

def guess_editor(config):
    for var in (
            "%s_VISUAL" % APP_NAME.upper(),
            "%s_EDITOR" % APP_NAME.upper(),
            "VISUAL",
            "EDITOR",
            ):
        default_editor = os.environ.get(var)
        if default_editor: break
    else:
        try:
            default_editor = config.get(CONF_SEC_CONFIG, CONF_EDITOR),
        except ConfigParser.NoOptionError:
            if IS_WINDOWS:
                default_editor = "edit"
            else:
                default_editor = "vi"
    return default_editor

def tweaking_options(config):
    """Adds options for modifying an item
    """
    parser = optparse.OptionParser()
    default_email = config.get(CONF_SEC_CONFIG, CONF_EMAIL)
    default_user = config.get(CONF_SEC_CONFIG, CONF_USERNAME)

    ALL_PRIORITIES = (
        PRIORITY_NAME_TO_NUMBER.keys() +
        map(str, PRIORITY_NUMBER_TO_NAME.keys())
        )
    parser.add_option("-p", "--priority",
        help="One of [%s], (default %r)" % (
            PRIORITY_STRING,
            DEF_PRIORITY_STR,
            ),
        dest=OPT_PRIORITY,
        action="callback",
        type="string",
        #nargs=2,
        callback=sloppy_choice_callback,
        callback_args=(ALL_PRIORITIES, ),
        default=DEF_PRIORITY_STR,
        )
    parser.add_option("-m", "--email",
        help="Email adddress of the submitter "
            "(default %r)" % default_email,
        dest=OPT_EMAIL,
        action="store",
        default=default_email,
        )
    parser.add_option("-n", "--no-message",
        help="Don't ask for details in $EDITOR",
        dest=OPT_GATHER_MESSAGE,
        default=True,
        action="store_false",
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
    parser.add_option("-e", "--editor",
        help="Editor",
        dest=CONF_EDITOR,
        action="store",
        default=guess_editor(config),
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
    for name, items in (
            (CONF_PENDING_CATEGORIES, DEF_PENDING_CATEGORIES),
            ):
        c.set(CONF_SEC_CONFIG, name, ','.join(sorted(items)))
    for name, value in (
            (CONF_EMAIL, LOCAL_EMAIL),
            (CONF_USERNAME, LOCAL_USERNAME),
            (CONF_DIRNAME, DEF_DIRNAME),
            (CONF_DONE_CATEGORY, DEF_DONE_CATEGORY),
            ):
        c.set(CONF_SEC_CONFIG, name, value)
    return c

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

def config_sanity_check(config):
    pass

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
    config_sanity_check(config)
    if cmd is None:
        parser.print_help()
        return 1
    if cmd not in CMD_MAP:
        rest = [cmd] + rest
        cmd = CMD_LIST
    log.debug("Command %r %r", cmd, rest)
    results = CMD_MAP[cmd](options, config, rest)
    for result in results:
        print str(result)

if __name__ == "__main__":
    sys.exit(main(*sys.argv))
