import collections
import copy

import six

from .hooks import Hooks
from .meta import ConfigManagerSettings
from .exceptions import NotFound
from .utils import not_set
from .base import BaseSection, is_config_item, is_config_section


_iter_emitters = {
    'path': lambda k, v, _: (k, v),
    'name': lambda k, v, _: (v.alias, v) if v.is_section else (v.name, v),
    'alias': lambda k, v, _: (v.alias, v) if v.is_section else (v.name, v),
    'str_path': lambda k, v, sep: (sep.join(k), v),
    None: lambda k, v: v,
}


class Section(BaseSection):
    """
    Core section functionality.

    Keep as light as possible.
    """

    def __init__(self, configmanager_settings=None):

        # It is Config's responsibility to initialise configmanager_settings.
        if configmanager_settings is None:
            configmanager_settings = ConfigManagerSettings()
        elif isinstance(configmanager_settings, dict):
            raise ValueError('configmanager_settings should be either None or an instance of ConfigManagerSettings')

        self._configmanager_settings = configmanager_settings

        self._configmanager_tree = collections.OrderedDict()
        self._configmanager_section = None
        self._configmanager_section_alias = None

        self._configmanager_hooks = Hooks(self)

    def __len__(self):
        return len(self._configmanager_tree)

    def __nonzero__(self):
        return True

    def __bool__(self):
        return True

    def __iter__(self):
        for name in self._configmanager_tree.keys():
            yield name

    def __repr__(self):
        return '<{cls} {alias} at {id}>'.format(cls=self.__class__.__name__, alias=self.alias, id=id(self))

    def _resolve_config_key(self, key):
        if isinstance(key, six.string_types):
            if key in self._configmanager_tree:
                return self._configmanager_tree[key]
            else:
                result = self.hooks.handle(Hooks.NOT_FOUND, name=key, section=self)
                if result is not None:
                    return result
                raise NotFound(key, section=self)

        if isinstance(key, (tuple, list)) and len(key) > 0:
            if len(key) == 1:
                return self._resolve_config_key(key[0])
            else:
                return self._resolve_config_key(key[0])[key[1:]]
        else:
            raise TypeError('Expected either a string or a tuple as key, got {!r}'.format(key))

    def __contains__(self, key):
        try:
            _ = self._resolve_config_key(key)
            return True
        except NotFound:
            return False

    def __setitem__(self, key, value):
        if isinstance(key, six.string_types):
            name = key
            rest = None
        elif isinstance(key, (tuple, list)) and len(key) > 0:
            name = key[0]
            if len(key) == 1:
                rest = None
            else:
                rest = key[1:]
        else:
            raise TypeError('Expected either a string or a tuple as key, got {!r}'.format(key))

        if rest:
            self[name][rest] = value
            return

        if is_config_item(value):
            self.add_item(name, value)
        elif is_config_section(value):
            self.add_section(name, value)
        else:
            raise TypeError(
                'Section sections/items can only be replaced with sections/items, '
                'got {type}. To set value use ..[{name}].value = <new_value>'.format(
                    type=type(value),
                    name=name,
                )
            )

    def __getitem__(self, key):
        return self._resolve_config_key(key)

    def __getattr__(self, name):
        if not isinstance(name, six.string_types):
            raise TypeError('Expected a string, got a {!r}'.format(type(name)))

        if name.startswith('_'):
            raise AttributeError(name)

        return self._resolve_config_key(name)

    def __setattr__(self, name, value):
        if name.startswith('configmanager_') or name.startswith('_configmanager_'):
            return super(Section, self).__setattr__(name, value)
        elif is_config_item(value):
            self.add_item(name, value)
        elif is_config_section(value):
            self.add_section(name, value)
        else:
            raise TypeError(
                'Section sections/items can only be replaced with sections/items, '
                'got {type}. To set value use {name}.value = <new_value> notation.'.format(
                    type=type(value),
                    name=name,
                )
            )

    @property
    def hooks(self):
        return self._configmanager_hooks

    @property
    def section(self):
        """
        Returns:
            (:class:`.Config`): section to which this section belongs or ``None`` if this
            hasn't been added to any section.
        """
        return self._configmanager_section

    @property
    def alias(self):
        """
        Returns alias with which this section was added to another or ``None`` if it hasn't been added
        to any.

        Returns:
            (str)
        """
        return self._configmanager_section_alias

    def add_item(self, alias, item):
        """
        Add a config item to this section.
        """
        if not isinstance(alias, six.string_types):
            raise TypeError('Item name must be a string, got a {!r}'.format(type(alias)))
        item = copy.deepcopy(item)
        if item.name is not_set:
            item.name = alias

        self._configmanager_tree[item.name] = item
        self._configmanager_tree[alias] = item

        item._section = self

        self.hooks.handle(Hooks.ITEM_ADDED_TO_SECTION, alias=alias, section=self, subject=item)

    def add_section(self, alias, section):
        """
        Add a sub-section to this section.
        """
        if not isinstance(alias, six.string_types):
            raise TypeError('Section name must be a string, got a {!r}'.format(type(alias)))

        self._configmanager_tree[alias] = section

        section._configmanager_section = self
        section._configmanager_section_alias = alias

        # Must not mess around with settings of other Config instances.
        if not section.is_config:
            section._configmanager_settings = self._configmanager_settings

        self.hooks.handle(Hooks.SECTION_ADDED_TO_SECTION, alias=alias, section=self, subject=section)

    def _get_str_path_separator(self, override=not_set):
        if override is not not_set:
            return override
        return self._configmanager_settings.str_path_separator

    def _parse_path(self, path=None, separator=not_set):
        if not path:
            return ()

        if isinstance(path, six.string_types):
            clean_path = tuple(path.split(self._get_str_path_separator(separator)))
        else:
            clean_path = path

        if clean_path not in self:
            for i, part in enumerate(clean_path):
                if clean_path[:i + 1] not in self:
                    raise NotFound(part)
            assert False  # shouldn't reach this line
        return clean_path

    def _get_recursive_iterator(self, recursive=False):
        """
        Basic recursive iterator whose only purpose is to yield all items
        and sections in order, with their full paths as keys.

        Main challenge is to de-duplicate items and sections which
        have aliases.

        Do not add any new features to this iterator, instead
        build others that extend this one.
        """

        names_yielded = set()

        for obj_alias, obj in self._configmanager_tree.items():
            if obj.is_section:
                if obj.alias in names_yielded:
                    continue
                names_yielded.add(obj.alias)

                yield (obj.alias,), obj

                if not recursive:
                    continue

                for sub_item_path, sub_item in obj._get_recursive_iterator(recursive=recursive):
                    yield (obj_alias,) + sub_item_path, sub_item

            else:
                # _configmanager_tree contains duplicates so that we can have multiple aliases point
                # to the same item. We have to de-duplicate here.
                if obj.name in names_yielded:
                    continue
                names_yielded.add(obj.name)

                yield (obj.name,), obj

    def _get_path_iterator(self, path=None, separator=not_set, recursive=False):
        clean_path = self._parse_path(path=path, separator=separator)

        config = self[clean_path] if clean_path else self

        if clean_path:
            yield clean_path, config

        if config.is_section:
            for path, obj in config._get_recursive_iterator(recursive=recursive):
                yield (clean_path + path), obj

    def iter_all(self, recursive=False, path=None, key='path', separator=not_set):
        """
        Args:
            recursive: if ``True``, recurse into sub-sections

            path (tuple or string): optional path to limit iteration over.

            key: ``path`` (default), ``str_path``, ``name``, ``None``, or a function to calculate the key from
                ``(k, v)`` tuple.

            separator (string): used both to interpret ``path=`` kwarg when it is a string,
                and to generate ``str_path`` as the returned key.

        Returns:
            iterator: iterator over ``(path, obj)`` pairs of all items and
            sections contained in this section.
        """
        if isinstance(key, six.string_types):
            if key in _iter_emitters:
                emitter = _iter_emitters[key]
            else:
                raise ValueError('Invalid key {!r}'.format(key))
        else:
            emitter = lambda k, v, _, f=key: (f(k, v), v)

        separator = self._get_str_path_separator(separator)

        for path, obj in self._get_path_iterator(recursive=recursive, path=path, separator=separator):
            yield emitter(path, obj, separator)

    def iter_items(self, recursive=False, path=None, key='path', separator=not_set):
        """

        See :meth:`.iter_all` for standard iterator argument descriptions.

        Returns:
            iterator: iterator over ``(key, item)`` pairs of all items
                in this section (and sub-sections if ``recursive=True``).

        """
        for x in self.iter_all(recursive=recursive, path=path, key=key, separator=separator):
            if key is None:
                if x.is_item:
                    yield x
            elif x[1].is_item:
                yield x

    def iter_sections(self, recursive=False, path=None, key='path', separator=not_set):
        """
        See :meth:`.iter_all` for standard iterator argument descriptions.

        Returns:
            iterator: iterator over ``(key, section)`` pairs of all sections
                in this section (and sub-sections if ``recursive=True``).

        """
        for x in self.iter_all(recursive=recursive, path=path, key=key, separator=separator):
            if key is None:
                if x.is_section:
                    yield x
            elif x[1].is_section:
                yield x

    def iter_paths(self, recursive=False, path=None, key='path', separator=not_set):
        """

        See :meth:`.iter_all` for standard iterator argument descriptions.

        Returns:
            iterator: iterator over paths of all items and sections
            contained in this section.

        """
        assert key is not None
        for path, _ in self.iter_all(recursive=recursive, path=path, key=key, separator=separator):
            yield path

    def reset(self):
        """
        Recursively resets values of all items contained in this section
        and its subsections to their default values.
        """
        for _, item in self.iter_items(recursive=True):
            item.reset()

    @property
    def is_default(self):
        """
        ``True`` if values of all config items in this section and its subsections
        have their values equal to defaults or have no value set.
        """
        for _, item in self.iter_items(recursive=True):
            if not item.is_default:
                return False
        return True

    def dump_values(self, with_defaults=True, dict_cls=dict, flat=False, separator=not_set):
        """
        Export values of all items contained in this section to a dictionary.

        Items with no values set (and no defaults set if ``with_defaults=True``) will be excluded.

        Returns:
            dict: A dictionary of key-value pairs, where for sections values are dictionaries
            of their contents.

        """
        values = dict_cls()

        if flat:
            for str_path, item in self.iter_items(recursive=True, separator=separator, key='str_path'):
                if item.has_value:
                    if with_defaults or not item.is_default:
                        values[str_path] = item.value
        else:
            for item_name, item in self._configmanager_tree.items():
                if is_config_section(item):
                    section_values = item.dump_values(with_defaults=with_defaults, dict_cls=dict_cls)
                    if section_values:
                        values[item_name] = section_values
                else:
                    if item.has_value:
                        if with_defaults or not item.is_default:
                            values[item.name] = item.value
        return values

    def load_values(self, dictionary, as_defaults=False, flat=False, separator=not_set):
        """
        Import config values from a dictionary.

        When ``as_defaults`` is set to ``True``, the values
        imported will be set as defaults. This can be used to
        declare the sections and items of configuration.
        Values of sections and items in ``dictionary`` can be
        dictionaries as well as instances of :class:`.Item` and
        :class:`.Config`.

        Args:
            dictionary:
            as_defaults: if ``True``, the imported values will be set as defaults.
        """
        separator = self._get_str_path_separator(separator)

        if flat:
            # Deflatten the dictionary and then pass on to the normal case.
            flat_dictionary = dictionary
            dictionary = collections.OrderedDict()
            for k, v in flat_dictionary.items():
                k_parts = k.split(separator)
                c = dictionary
                for i, kp in enumerate(k_parts):
                    if i >= len(k_parts) - 1:
                        c[kp] = v
                    else:
                        if kp not in c:
                            c[kp] = collections.OrderedDict()
                        c = c[kp]

        for name, value in dictionary.items():
            if name not in self:
                if as_defaults:
                    if isinstance(value, dict):
                        self[name] = self.create_section()
                        self[name].load_values(value, as_defaults=as_defaults)
                    else:
                        self[name] = self.create_item(name, default=value)
                else:
                    # Skip unknown names if not interpreting dictionary as defaults
                    continue
            elif is_config_item(self[name]):
                if as_defaults:
                    self[name].default = value
                else:
                    self[name].value = value
            else:
                self[name].load_values(value, as_defaults=as_defaults)

    def create_item(self, *args, **kwargs):
        """
        Internal factory method used to create an instance of configuration item.
        Should only be used to extend configmanager's functionality.
        """
        return self._configmanager_settings.item_cls(*args, **kwargs)

    def create_section(self, *args, **kwargs):
        """
        Internal factory method used to create an instance of configuration section.
        Should only be used to extend configmanager's functionality.
        """
        kwargs.setdefault('configmanager_settings', self._configmanager_settings)
        return self._configmanager_settings.section_cls(*args, **kwargs)
