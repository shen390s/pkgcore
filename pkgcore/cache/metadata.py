# Copyright: 2005 Brian Harring <ferringb@gmail.com>
# License: GPL2

"""
cache backend designed for rsynced tree's pregenerated metadata.
"""

import os
import errno
from pkgcore.cache import flat_hash, errors
from pkgcore.config import ConfigHint
from pkgcore.ebuild import eclass_cache
from pkgcore.util.mappings import ProtectedDict


# store the current key order *here*.
class database(flat_hash.database):
    """
    Compatibility with (older) portage-generated caches.

    Autodetects per entry if it is a
    L{flat_list<pkgcore.cache.flat_hash.database>} and flat_list
    backends entry, and converts old (and incomplete) INHERITED field
    to _eclasses_ as required.
    """
    complete_eclass_entries = False

    auxdbkeys_order = ('DEPEND', 'RDEPEND', 'SLOT', 'SRC_URI',
        'RESTRICT',  'HOMEPAGE',  'LICENSE', 'DESCRIPTION',
        'KEYWORDS',  'INHERITED', 'IUSE', 'CDEPEND',
        'PDEPEND',   'PROVIDE', 'EAPI')
    
    # this is the old cache format, flat_list.  hardcoded, and must
    # remain that way.
    magic_line_count = 22

    autocommits = True

    def __init__(self, location, *args, **config):
        self.base_loc = location
        super(database, self).__init__(location, *args, **config)
        self.ec = eclass_cache.cache(os.path.join(self.base_loc, "eclass"),
            self.base_loc)
        self.hardcoded_auxdbkeys_order = tuple((idx, key)
            for idx, key in enumerate(self.auxdbkeys_order)
                if key in self._known_keys)

    __init__.__doc__ = flat_hash.database.__init__.__doc__.replace(
        "@keyword location", "@param location")


    def _format_location(self):
        return os.path.join(self.location, "metadata", "cache")

    def __getitem__(self, cpv):
        d = flat_hash.database.__getitem__(self, cpv)

        if "_eclasses_" not in d:
            if "INHERITED" in d:
                d["_eclasses_"] = self.ec.get_eclass_data(
                    d["INHERITED"].split())
                del d["INHERITED"]
        else:
            d["_eclasses_"] = self.reconstruct_eclasses(cpv, d["_eclasses_"])

        return d

    def _parse_data(self, data, mtime):
        # easy attempt first.
        data = list(data)
        if len(data) != self.magic_line_count:
            raise errors.GeneralCacheCorruption("wrong line count")

        # this one's interesting.
        d = self._cdict_kls()
        for idx, key in self.hardcoded_auxdbkeys_order:
            d[key] = data[idx].strip()

        if self._mtime_used:
            d["_mtime_"] = mtime
        return d

    def _setitem(self, cpv, values):
        values = ProtectedDict(values)

        # hack. proper solution is to make this a __setitem__ override, since
        # template.__setitem__ serializes _eclasses_, then we reconstruct it.
        if "_eclasses_" in values:
            values["INHERITED"] = ' '.join(
                self.reconstruct_eclasses(cpv, values["_eclasses_"]).keys())
            del values["_eclasses_"]

        flat_hash.database._setitem(self, cpv, values)


class flat_list(database):

    """(Hopefully) write a flat_list format cache. Not very well tested."""

    pkgcore_config_type = ConfigHint(
        {'readonly': 'bool', 'location': 'str', 'label': 'str'},
        required=['location', 'label'],
        positional=['location', 'label'],
        typename='cache')

    def __init__(self, location, *args, **config):
        config['auxdbkeys'] = self.auxdbkeys_order
        database.__init__(self, location, *args, **config)

    def _setitem(self, cpv, values):
        values = ProtectedDict(values)

        # hack. proper solution is to make this a __setitem__ override, since
        # template.__setitem__ serializes _eclasses_, then we reconstruct it.
        eclasses = values.pop('_eclasses_', None)
        if eclasses is not None:
            eclasses = self.reconstruct_eclasses(cpv, eclasses)
            values["INHERITED"] = ' '.join(eclasses)

        s = cpv.rfind("/")
        fp = os.path.join(
            self.location, cpv[:s],".update.%i.%s" % (os.getpid(), cpv[s+1:]))
        try:
            myf=open(fp, "w")
        except (OSError, IOError), e:
            if errno.ENOENT == e.errno:
                try:
                    self._ensure_dirs(cpv)
                    myf=open(fp,"w")
                except (OSError, IOError),e:
                    raise errors.CacheCorruption(cpv, e)
            else:
                raise errors.CacheCorruption(cpv, e)

        for x in self.auxdbkeys_order:
            myf.write(values.get(x,"")+"\n")

        myf.close()
        if eclasses:
            self._ensure_access(
                fp,
                mtime=max(max(mtime for path, mtime in eclasses.itervalues()),
                          values["_mtime_"]))
        else:
            self._ensure_access(fp, values["_mtime_"])

        #update written.  now we move it.
        new_fp = os.path.join(self.location, cpv)
        try:
            os.rename(fp, new_fp)
        except (OSError, IOError), e:
            os.remove(fp)
            raise errors.CacheCorruption(cpv, e)


class protective_database(database):

    def _parse_data(self, data, mtime):
        # easy attempt first.
        data = list(data)
        if len(data) != self.magic_line_count:
            return flat_hash.database._parse_data(self, data, mtime)

        # this one's interesting.
        d = self._cdict_kls()

        for line in data:
            # yes, meant to iterate over a string.
            hashed = False
            for idx, c in enumerate(line):
                if not c.isalpha():
                    if c == "=" and idx > 0:
                        hashed = True
                        d[line[:idx]] = line[idx + 1:]
                    elif c == "_" or c.isdigit():
                        continue
                    break
                elif not c.isupper():
                    break

            if not hashed:
                # non hashed.
                d.clear()
                for idx, key in self.hardcoded_auxdbkeys_order:
                    d[key] = data[idx].strip()
                break

        if self._mtime_used:
            d["_mtime_"] = mtime
        return d


