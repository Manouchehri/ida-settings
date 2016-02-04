"""
ida_settings provides a mechanism for settings and fetching
configration values for IDAPython scripts and plugins.
Configurations are namespaced by plugin name,
and scoped to the global system, current user,
working directory, or current IDB file. Configurations
can be exported and imported using an .ini-style intermediate
representation.

Example fetching a configuration value:

    settings = IDASettings("MSDN-doc")
    if settings["verbosity"] == "high":
        ...

Example setting a global configuration value:

    settings = IDASettings("MSDN-doc")
    settings.system["verbosity"] = "high"

Example setting a working directory configuration value:

    settings = IDASettings("MSDN-doc")
    settings.directory["verbosity"] = "high"

Use the properties "system", "user", "directory" and "idb"
to scope configuration accesses and mutations to the global
system, current user, working directory, or current IDB file.

Treat a settings instance like a dictionary. For example:

    settings = IDASettings("MSDN-doc")
    "verbosity" in settings --> False
    settings["verbosity"] = "high"
    settings["verbosity"] --> "high"
    settings.keys() --> ["verbosity"]
    settings.values() --> ["high"]
    settings.items() --> [("verbosity", "high')]

To export the current effective settings, use the `export_settings`
function. For example:

    settings = IDASettings("MSDN-doc")
    export_settings(settings, "/home/user/desktop/current.ini")

To import existing settings into a settings instance, such as
the open IDB, use the `import_settings` function. For example:

    settings = IDASettings("MSDN-doc")
    import_settings(settings.idb, "/home/user/desktop/current.ini")

This module is a single file that you can include in IDAPython
plugin module or scripts. It is licensed under the Apache 2.0
license.

Author: Willi Ballenthin <william.ballenthin@fireeye.com>
"""
import os
import re
import abc
import unittest
import contextlib

import idc
import idaapi
try:
    from PyQt5 import QtCore
except ImportError:
    from PySide import QtCore


# enforce methods required by settings providers
class IDASettingsInterface:
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def get_value(self, key):
        # type key: basestring
        # rtype value: bytes
        raise NotImplemented

    @abc.abstractmethod
    def set_value(self, key, value):
        # type key: basestring
        # type value: bytes
        raise NotImplemented

    @abc.abstractmethod
    def del_value(self, key):
        # type key: basestring
        raise NotImplemented

    @abc.abstractmethod
    def get_keys(self):
        # rtype: Iterable[basestring]
        raise NotImplemented


# filenames can be alphanumeric, with spaces, dashes, and periods
FILENAME_RE = re.compile("[A-Za-z0-9 \-\.]+$")
def validate(s):
    return FILENAME_RE.match(s) != None


# provide base constructor args required by settings providers
class IDASettingsBase(IDASettingsInterface):
    def __init__(self, plugin_name):
        super(IDASettingsBase, self).__init__()
        if not validate(plugin_name):
            raise RuntimeError("invalid plugin name")
        self._plugin_name = plugin_name


# allow IDASettings to look like dicts
class DictMixin:
    def __getitem__(self, key):
        if not isinstance(key, basestring):
            raise TypeError("key must be a string")
        return self.get_value(key)

    def __setitem__(self, key, value):
        if not isinstance(key, basestring):
            raise TypeError("key must be a string")
        if not isinstance(value, bytes):
            raise TypeError("value must be a bytes")
        return self.set_value(key, value)

    def __delitem__(self, key):
        if not isinstance(key, basestring):
            raise TypeError("key must be a string")
        return self.del_value(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return Default

    def __contains__(self, key):
        try:
            if self[key] is not None:
                return True
            return False
        except KeyError:
            return False

    def iterkeys(self):
        return self.get_keys()

    def keys(self):
        return [k for k in self.iterkeys()]

    def itervalues(self):
        for k in self.iterkeys():
            yield self[k]

    def values(self):
        return [v for v in self.itervalues()]

    def iteritems(self):
        for k in self.iterkeys():
            yield k, self[k]

    def items(self):
        return [(k, v) for k, v in self.iteritems()]


IDA_SETTINGS_ORGANIZATION = "IDAPython"
IDA_SETTINGS_APPLICATION = "IDA-Settings"


def get_qsettings(scope, group=None):
    s = QtCore.QSettings(scope, IDA_SETTINGS_ORGANIZATION, IDA_SETTINGS_APPLICATION)
    if group is not None:
        s.beginGroup(group)
    return s
    

class SystemIDASettings(IDASettingsBase, DictMixin):
    @property
    def _settings(self):
        return get_qsettings(QtCore.QSettings.SystemScope, group=self._plugin_name)

    def get_value(self, key):
        v = self._settings.value(key)
        if v is None:
            raise KeyError("key not found")
        return v

    def set_value(self, key, value):
        return self._settings.setValue(key, value)

    def del_value(self, key):
        return self._settings.remove(key)

    def get_keys(self):
        for k in self._settings.allKeys():
            yield k


class UserIDASettings(IDASettingsBase, DictMixin):
    @property
    def _settings(self):
        return get_qsettings(QtCore.QSettings.UserScope, group=self._plugin_name)

    def get_value(self, key):
        # apparently QSettings falls back to System scope here?
        v = self._settings.value(key)
        if v is None:
            raise KeyError("key not found")
        return v

    def set_value(self, key, value):
        return self._settings.setValue(key, value)

    def del_value(self, key):
        return self._settings.remove(key)

    def get_keys(self):
        for k in self._settings.allKeys():
            yield k


def get_current_directory_config_path():
    directory = os.path.dirname(idc.GetIdbPath())
    config_name = ".ida-settings.ini"
    config_path = os.path.join(directory, config_name)
    return config_path


class DirectoryIDASettings(IDASettingsBase, DictMixin):
    @property
    def _settings(self):
        s = QtCore.QSettings(get_current_directory_config_path(), QtCore.QSettings.IniFormat)
        s.beginGroup(self._plugin_name)
        return s

    def get_value(self, key):
        v = self._settings.value(key)
        if v is None:
            raise KeyError("key not found")
        return v

    def set_value(self, key, value):
        return self._settings.setValue(key, value)

    def del_value(self, key):
        return self._settings.remove(key)

    def get_keys(self):
        for k in self._settings.allKeys():
            yield k


def get_meta_netnode():
    """
    Get the netnode used to store settings metadata in the current IDB.
    Note that this implicitly uses the open IDB via the idc iterface.
    """
    node_name = "$ {org:s}.{application:s}".format(
        org=IDA_SETTINGS_ORGANIZATION,
        application=IDA_SETTINGS_APPLICATION)
    # namelen: 0, do_create: True
    return idaapi.netnode(node_name, 0, True)


PLUGIN_NAMES_KEY = "plugin_names"


def get_netnode_plugin_names():
    """
    Get a iterable of the plugin names registered in the current IDB.
    Note that this implicitly uses the open IDB via the idc iterface.
    """
    n = get_meta_netnode()

    try:
        v = n.hashval(PLUGIN_NAMES_KEY)
    except TypeError:
        return []
    if v is None:
        return []
    return json.loads(v)


def add_netnode_plugin_name(plugin_name):
    """
    Add the given plugin name to the list of plugin names registered in
      the current IDB.
    Note that this implicitly uses the open IDB via the idc iterface.
    """
    current_names = set(get_current_plugin_names())
    if plugin_name in current_names:
        return

    current_names.add(plugin_name)

    n = get_meta_netnode()
    n.hashset(PLUGIN_NAMES_KEY, json.dumps(list(current_names)))


class IDBIDASettings(IDASettingsBase, DictMixin):
    @property
    def _netnode(self):
        node_name = "$ {org:s}.{application:s}.{plugin_name:s}".format(
            org=IDA_SETTINGS_ORGANIZATION,
            application=IDA_SETTINGS_APPLICATION,
            plugin_name=self._plugin_name)
        # namelen: 0, do_create: True
        n = idaapi.netnode(node_name, 0, True)
        add_netnode_plugin_name(self._plugin_name)
        return n

    def get_value(self, key):
        if not isinstance(key, basestring):
            raise TypeError("key must be a string")

        try:
            v = self._netnode.hashval(key)
        except TypeError:
            raise KeyError("key not found")
        if v is None:
            raise KeyError("key not found")

        return v

    def set_value(self, key, value):
        if not isinstance(key, basestring):
            raise TypeError("key must be a string")

        if not isinstance(value, bytes):
            raise TypeError("value must be a bytes")

        self._netnode.hashset(key, value)

    def del_value(self, key):
        if not isinstance(key, basestring):
            raise TypeError("key must be a string")

        self._netnode.hashdel(key)
        self._netnode.hashset(key, None)

    def get_keys(self):
        k = self._netnode.hash1st()
        while k != idaapi.BADNODE and k is not None:
            yield k
            k = self._netnode.hashnxt(k)


class IDASettings(object):
    def __init__(self, plugin_name):
        super(IDASettings, self).__init__()
        if not validate(plugin_name):
            raise RuntimeError("invalid plugin name")
        self._plugin_name = plugin_name

    @property
    def idb(self):
        return IDBIDASettings(self._plugin_name)

    @property
    def directory(self):
        return DirectoryIDASettings(self._plugin_name)

    @property
    def user(self):
        return UserIDASettings(self._plugin_name)

    @property
    def system(self):
        return SystemIDASettings(self._plugin_name)

    def get_value(self, key):
        try:
            return self.idb.get_value(key)
        except KeyError:
            pass
        try:
            return self.directory.get_value(key)
        except KeyError:
            pass
        try:
            return self.user.get_value(key)
        except KeyError:
            pass
        try:
            return self.system.get_value(key)
        except KeyError:
            pass

        raise KeyError("key not found")

    @property
    def system_plugin_names(self):
        """
        directory = os.path.dirname(idc.GetIdbPath())
        config_name = ".ida-settings.ini"
        config_path = os.path.join(directory, config_name)
        s = QtCore.QSettings(config_path, QtCore.QSettings.IniFormat)
        s.beginGroup(self._plugin_name)

        node_name = "$ {org:s}.{application:s}.{plugin_name:s}".format(
            org=IDA_SETTINGS_ORGANIZATION,
            application=IDA_SETTINGS_APPLICATION,
            plugin_name=self._plugin_name)
        # namelen: 0, do_create: True
        return idaapi.netnode(node_name, 0, True)
        return s
        """
        s = get_qsettings(QtCore.QSettings.SystemScope)
        return s.childGroups()[:]

    @property
    def user_plugin_names(self):
        s = get_qsettings(QtCore.QSettings.UserScope)
        return s.childGroups()[:]
 
    @property
    def directory_plugin_names(self):
        s = QtCore.QSettings(get_current_directory_config_path(), QtCore.QSettings.IniFormat)
        return s.childGroups()[:]
 
    @property
    def idb_plugin_names(self):
        return get_netnode_plugin_names()

 
def import_settings(settings, path):
    other = QtCore.QSettings(config_path, QtCore.QSettings.IniFormat)
    for k in other.allKeys():
        settings[k] = other.value(k)


def export_settings(settings, path):
    other = QtCore.QSettings(config_path, QtCore.QSettings.IniFormat)
    for k, v in settings.iteritems():
        other.setValue(k, v)


PLUGIN_1 = "plugin1"
PLUGIN_2 = "plugin2"
KEY_1 = "key_1"
KEY_2 = "key_2"
VALUE_1 = bytes("hello")
VALUE_2 = bytes("goodbye")


class TestSync(unittest.TestCase):
    """
    Demonstrate that creating new instances of the settings objects shows the same data.
    """
    def test_system(self):
        # this may fail if the user is not running as admin
        IDASettings(PLUGIN_1).system.set_value(KEY_1, VALUE_1)
        self.assertEqual(IDASettings(PLUGIN_1).system.get_value(KEY_1), VALUE_1)

    def test_user(self):
        IDASettings(PLUGIN_1).user.set_value(KEY_1, VALUE_1)
        self.assertEqual(IDASettings(PLUGIN_1).system.get_value(KEY_1), VALUE_1)

    def test_directory(self):
        IDASettings(PLUGIN_1).directory.set_value(KEY_1, VALUE_1)
        self.assertEqual(IDASettings(PLUGIN_1).directory.get_value(KEY_1), VALUE_1)

    def test_idb(self):
        IDASettings(PLUGIN_1).idb.set_value(KEY_1, VALUE_1)
        self.assertEqual(IDASettings(PLUGIN_1).idb.get_value(KEY_1), VALUE_1)



@contextlib.contextmanager
def clearing(test):
    test.clear()
    try:
        yield
    finally:
        test.clear()


class TestSettingsMixin(object):
    """
    A mixin that adds standard tests test cases with:
      - self.clear()
      - self.settings, an IDASettingsInterface implementor
    """
    def test_set(self):
        with clearing(self):
            # simple set
            self.settings.set_value(KEY_1, VALUE_1)
            self.assertEqual(self.settings.get_value(KEY_1), VALUE_1)
            # overwrite
            self.settings.set_value(KEY_1, VALUE_2)
            self.assertEqual(self.settings.get_value(KEY_1), VALUE_2)

    def test_del(self):
        with clearing(self):
            self.settings.set_value(KEY_1, VALUE_1)
            self.settings.del_value(KEY_1)
            with self.assertRaises(KeyError):
                self.settings.get_value(KEY_1)

    def test_keys(self):
        with clearing(self):
            self.settings.del_value(KEY_1)
            self.settings.del_value(KEY_2)
            self.settings.set_value(KEY_1, VALUE_1)
            self.assertEqual(list(self.settings.get_keys()), [KEY_1])
            self.settings.set_value(KEY_2, VALUE_2)
            self.assertEqual(list(self.settings.get_keys()), [KEY_1, KEY_2])

    def test_dict(self):
        with clearing(self):
            self.assertFalse(KEY_1 in self.settings)
            self.settings[KEY_1] = VALUE_1
            self.assertEquals(self.settings[KEY_1], VALUE_1)
            self.settings[KEY_2] = VALUE_2
            self.assertEquals(self.settings[KEY_2], VALUE_2)
            self.assertEquals(self.settings.keys(), [KEY_1, KEY_2])
            self.assertEquals(self.settings.values(), [VALUE_1, VALUE_2])
            del self.settings[KEY_1]
            self.assertEquals(self.settings.keys(), [KEY_2])


class TestSystemSettings(unittest.TestCase, TestSettingsMixin):
    def setUp(self):
        self.settings = IDASettings(PLUGIN_1).system

    def clear(self):
        # cheating, sorry
        self.settings._settings.clear()


class TestUserSettings(unittest.TestCase, TestSettingsMixin):
    def setUp(self):
        self.settings = IDASettings(PLUGIN_1).user

    def clear(self):
        # cheating, sorry
        self.settings._settings.clear()


class TestDirectorySettings(unittest.TestCase, TestSettingsMixin):
    def setUp(self):
        self.settings = IDASettings(PLUGIN_1).directory

    def clear(self):
        # cheating, sorry
        self.settings._settings.clear()

class TestIdbSettings(unittest.TestCase, TestSettingsMixin):
    def setUp(self):
        self.settings = IDASettings(PLUGIN_1).idb

    def clear(self):
        # cheating, sorry
        self.settings._netnode.kill()


class TestUserAndSystemSettings(unittest.TestCase):
    def setUp(self):
        self.system = IDASettings(PLUGIN_1).system
        self.user = IDASettings(PLUGIN_1).user

    def clear(self):
        # cheating, sorry
        self.system._settings.clear()
        self.user._settings.clear()

    def test_system_fallback(self):
        """
        QSettings instances with scope "user" automatically fall back to
         scope "system" if the key doesn't exist.
        """
        with clearing(self):
            self.system.set_value(KEY_1, VALUE_1)
            self.assertEqual(self.user.get_value(KEY_1), VALUE_1)


class TestUserAndSystemSettings(unittest.TestCase):
    def setUp(self):
        self.system = IDASettings(PLUGIN_1).system
        self.user = IDASettings(PLUGIN_1).user
        self.directory = IDASettings(PLUGIN_1).directory
        self.idb = IDASettings(PLUGIN_1).idb
        self.mux = IDASettings(PLUGIN_1)

    def clear(self):
        # cheating, sorry
        self.system._settings.clear()
        self.user._settings.clear()
        self.directory._settings.clear()
        self.idb._netnode.kill()

    def test_user_gt_system(self):
        with clearing(self):
            self.system.set_value(KEY_1, VALUE_1)
            self.user.set_value(KEY_1, VALUE_2)
            self.assertEqual(self.mux.get_value(KEY_1), VALUE_2)

    def test_directory_gt_user(self):
        with clearing(self):
            self.user.set_value(KEY_1, VALUE_1)
            self.directory.set_value(KEY_1, VALUE_2)
            self.assertEqual(self.mux.get_value(KEY_1), VALUE_2)

    def test_idb_gt_directory(self):
        with clearing(self):
            self.directory.set_value(KEY_1, VALUE_1)
            self.idb.set_value(KEY_1, VALUE_2)
            self.assertEqual(self.mux.get_value(KEY_1), VALUE_2)


def main():
    try:
        unittest.main()
    except SystemExit:
        pass


if __name__ == "__main__":
    main()
