#!/usr/bin/env python
from socket import gethostname
import argparse
import collections
import ConfigParser
import getpass
import sys
import unittest

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

##################################################
# Defaults
##################################################
DEF_PENDING_CATEGORIES = set([
    "bugs",
    "features",
    ])
DEF_DONE_CATEGORIES = set([
    "done",
    ])
DEF_PRIORITY = "normal"
DEF_PRIORITIES = set([
    "high",
    DEF_PRIORITY,
    "low",
    ])

##################################################
# configuration .ini settings
##################################################
CONF_SEC_CONFIG = "config"
CONF_EMAIL = "email"
CONF_NAME = "name"
CONF_DIRNAME = "todo"
CONF_PENDING_CATEGORIES = "pending_categories"
CONF_DONE_CATEGORIES = "done_categories"
CONF_DEF_PRIORITY = "default_priority"
CONF_PRIORITIES = "priorities"

def get_parser():
    parser = argparse.ArgumentParser()
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
            (CONF_DONE_CATEGORIES, DEF_DONE_CATEGORIES),
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

class Todo(object):
    def __init__(self, desc,
            author=LOCAL_USERNAME,
            email=LOCAL_EMAIL,
            priority=DEF_PRIORITY,
            ):
        self.desc = desc

class TodoList(object):
    pass

class TestCommands(unittest.TestCase):
    def setUp(self):
        self.config = get_default_config()
        self.parser = get_parser()
    def test_dump_config(self):
        self.config.write(sys.stdout)
    def test_complete(self):
        "List of subfunction possibilities"
    def test_add(self):
        "Add a task"
    def test_edit(self):
        "Edit a task"
    def test_add_comment(self):
        "Add a comment"

if __name__ == "__main__":
    unittest.main()
