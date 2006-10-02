# Copyright: 2005 Brian Harring <ferringb@gmail.com>
# License: GPL2

"""
package class for buildable ebuilds
"""

import os, operator
from pkgcore.package import metadata, errors

WeakValCache = metadata.WeakValCache

from pkgcore.ebuild.cpv import CPV
from pkgcore.ebuild import conditionals
from pkgcore.ebuild.atom import atom
from pkgcore.ebuild.digest import parse_digest
from pkgcore.util.mappings import IndeterminantDict
from pkgcore.util.currying import post_curry, alias_class_method, partial
from pkgcore.restrictions.packages import AndRestriction
from pkgcore.restrictions import boolean
from pkgcore.chksum.errors import MissingChksum
from pkgcore.fetch.errors import UnknownMirror
from pkgcore.fetch import fetchable, mirror, uri_list, default_mirror
from pkgcore.ebuild import const, processor
from pkgcore.util.demandload import demandload
demandload(globals(), "errno ")


def generate_depset(s, c, *keys, **kwds):
    if kwds.pop("non_package_type", False):
        kwds["operators"] = {"||":boolean.OrRestriction,
                             "":boolean.AndRestriction}
    try:
        return conditionals.DepSet(" ".join([s.data.pop(x.upper(), "")
            for x in keys]), c, **kwds)
    except conditionals.ParseError, p:
        raise errors.MetadataException(s, str(keys), str(p))

def generate_providers(self):
    rdep = AndRestriction(self.versioned_atom, finalize=True)
    func = partial(virtual_ebuild, self._parent, self,
                   {"rdepends":rdep, "slot":self.version})
    # re-enable license at some point.
    #, "license":self.license})

    try:
        return conditionals.DepSet(
            self.data.pop("PROVIDE", ""), virtual_ebuild, element_func=func,
            operators={"||":boolean.OrRestriction,"":boolean.AndRestriction})

    except conditionals.ParseError, p:
        raise errors.MetadataException(self, "provide", str(p))

def generate_fetchables(self):
    chksums = self.repo._get_digests(self)

    mirrors = getattr(self._parent, "mirrors", {})
    default_mirrors = getattr(self._parent, "default_mirrors", None)
    common = {}
    try:
        d = conditionals.DepSet(
            self.data.pop("SRC_URI", ""), fetchable, operators={},
            element_func=partial(create_fetchable_from_uri, self, chksums,
                                 mirrors, default_mirrors, common))
        for v in common.itervalues():
            v.uri.finalize()
        return d
    except conditionals.ParseError, p:
        raise errors.MetadataException(self, "src_uri", str(p))

# utility func.
def create_fetchable_from_uri(pkg, chksums, mirrors, default_mirrors,
     common_files, uri):

    filename = os.path.basename(uri)

    preexisting = filename in common_files

    if not preexisting:
        if filename not in chksums:
            raise MissingChksum(filename)
        uris = uri_list(filename)
    else:
        uris = common_files[filename].uri
        
    if filename == uri:
        uris.add_uri(filename)
    else:
        if not preexisting:
            if "primaryuri" in pkg.restrict:
                uris.add_uri(uri)

            if default_mirrors and "mirror" not in pkg.restrict:
                uris.add_mirror(default_mirrors)

        if uri.startswith("mirror://"):
            # mirror:// is 9 chars.

            tier, remaining_uri = uri[9:].split("/", 1)

            if tier not in mirrors:
                raise UnknownMirror(tier, remaining_uri)

            uris.add_mirror(mirrors[tier])

        else:
            uris.add_uri(uri)

    if not preexisting:
        common_files[filename] = fetchable(filename, uris, chksums[filename])
    return common_files[filename]

def generate_eapi(self):
    try:
        d = self.data.pop("EAPI", 0)
        if d == "":
            return 0
        return int(d)
    except ValueError:
        return const.unknown_eapi

def rewrite_restrict(restrict):
    l = set()
    for x in restrict:
        if x.startswith("no"):
            l.add(x[2:])
        else:
            l.add(x)
    return tuple(l)


def get_slot(self):
    o = self.data.pop("SLOT", "0").strip()
    if not o:
        raise ValueError(self, "SLOT cannot be unset")
    return o

class base(metadata.package):

    """
    ebuild package

    @cvar tracked_attributes: sequence of attributes that are required to exist
        in the built version of ebuild-src
    @cvar _config_wrappables: mapping of attribute to callable for
        re-evaluating attributes dependant on configuration
    """

    tracked_attributes = (
        "depends", "rdepends", "post_rdepends", "provides", "license",
        "slot", "keywords", "eapi", "restrict", "eapi", "description", "iuse")

    _config_wrappables = dict((x, alias_class_method("evaluate_depset"))
        for x in ["depends", "rdepends", "post_rdepends", "fetchables",
                  "license", "src_uri", "license", "provides"])

    _get_attr = dict(metadata.package._get_attr)
    _get_attr["provides"] = generate_providers
    _get_attr["depends"] = post_curry(generate_depset, atom, "depend")
    _get_attr["rdepends"] = post_curry(generate_depset, atom, "rdepend")
    _get_attr["post_rdepends"] = post_curry(generate_depset, atom, "pdepend")
    _get_attr["license"] = post_curry(generate_depset,
        intern, "license", non_package_type=True)
    _get_attr["slot"] = get_slot # lambda s: s.data.pop("SLOT", "0").strip()
    _get_attr["fetchables"] = generate_fetchables
    _get_attr["description"] = lambda s:s.data.pop("DESCRIPTION", "").strip()
    _get_attr["keywords"] = lambda s:tuple(map(intern,
        s.data.pop("KEYWORDS", "").split()))
    _get_attr["restrict"] = lambda s:rewrite_restrict(
            s.data.pop("RESTRICT", "").split())
    _get_attr["eapi"] = generate_eapi
    _get_attr["iuse"] = lambda s:tuple(map(intern,
        s.data.pop("IUSE", "").split()))
    _get_attr["homepage"] = lambda s:s.data.pop("HOMEPAGE", "").strip()

    __slots__ = tuple(_get_attr.keys() + ["_pkg_metadata_shared"])

    @property
    def P(self):
        return "%s-%s" % (self.package, self.version)
    
    @property
    def PF(self):
        return "%s-%s" % (self.package, self.fullver)

    @property
    def PN(self):
        return self.package

    @property
    def PR(self):
        r = self.revision
        if r is not Nne:
            return r
        return 0

    @property
    def ebuild(self):
        return self._parent.get_ebuild_src(self)
    
    def _fetch_metadata(self):
        d = self._parent._get_metadata(self)
        return d

    def __str__(self):
        return "ebuild src: %s" % self.cpvstr

    def __repr__(self):
        return "<%s cpv=%r @%#8x>" % (self.__class__, self.cpvstr, id(self))


class package(base):
    
    __slots__ = ("_shared_pkg_data")
    
    _get_attr = dict(base._get_attr)
    
    def __init__(self, shared_pkg_data, *args, **kwargs):
        base.__init__(self, *args, **kwargs)
        object.__setattr__(self, "_shared_pkg_data", shared_pkg_data)
        
    @property
    def maintainers(self):
        return self._shared_pkg_data.metadata_xml.maintainers
    
    @property
    def herds(self):
        return self._shared_pkg_data.metadata_xml.herds
    
    @property
    def longdescription(self):
        return self._shared_pkg_data.metadata_xml.longdescription
    
    @property
    def _mtime_(self):
        return self._parent._get_ebuild_mtime(self)

    @property
    def manifest(self):
        return self._shared_pkg_data.manifest


class package_factory(metadata.factory):
    child_class = package

    def __init__(self, parent, cachedb, eclass_cache, mirrors, default_mirrors,
                 *args, **kwargs):
        super(package_factory, self).__init__(parent, *args, **kwargs)
        self._cache = cachedb
        self._ecache = eclass_cache
        if mirrors:
            mirrors = dict((k, mirror(v, k)) for k,v in mirrors.iteritems())

        self.mirrors = mirrors
        if default_mirrors:
            self.default_mirrors = default_mirror(default_mirrors,
                "conf. default mirror")
        else:
            self.default_mirrors = None

    def get_ebuild_src(self, pkg):
        return self._parent_repo._get_ebuild_src(pkg)

    def _get_metadata(self, pkg):
        for cache in self._cache:
            if cache is not None:
                try:
                    data = cache[pkg.cpvstr]
                except KeyError:
                    continue
                if long(data.pop("_mtime_", -1)) != pkg._mtime_ or \
                    self._invalidated_eclasses(data, pkg):
                    continue
                return data

        # no cache entries, regen
        return self._update_metadata(pkg)

    def _invalidated_eclasses(self, data, pkg):
        return (data.get("_eclasses_") is not None and not
            self._ecache.is_eclass_data_valid(data["_eclasses_"]))

    def _get_ebuild_path(self, pkg):
        return self._parent_repo._get_ebuild_path(pkg)

    def _get_ebuild_mtime(self, pkg):
        return long(os.stat(self._get_ebuild_path(pkg)).st_mtime)

    def _update_metadata(self, pkg):
        ebp = processor.request_ebuild_processor()
        try:
            mydata = ebp.get_keys(pkg, self._ecache)
        finally:
            processor.release_ebuild_processor(ebp)

        mydata["_mtime_"] = pkg._mtime_
        if mydata.get("INHERITED", False):
            mydata["_eclasses_"] = self._ecache.get_eclass_data(
                mydata["INHERITED"].split())
            del mydata["INHERITED"]
        else:
            mydata["_eclasses_"] = {}

        if self._cache is not None:
            for cache in self._cache:
                if not cache.readonly:
                    cache[pkg.cpvstr] = mydata
                    break

        return mydata

    def new_package(self, *args):
        inst = self._cached_instances.get(args, None)
        if inst is None:
            # key being cat/pkg
            mxml = self._parent_repo._get_shared_pkg_data(args[0], args[1])
            inst = self._cached_instances[args] = self.child_class(
                mxml, self, *args)
        return inst


generate_new_factory = package_factory


class virtual_ebuild(metadata.package):

    """
    PROVIDES generated fake packages
    """

    package_is_real = False
    built = True

    __slots__ = ("_orig_data", "data", "actual_pkg")

    def __init__(self, parent_repository, pkg, data, cpvstr):
        """
        @param cpvstr: cpv for the new pkg
        @param parent_repository: actual repository that this pkg should
            claim it belongs to
        @param pkg: parent pkg that is generating this pkg
        @param data: mapping of data to push to use in __getattr__ access
        """
        c = CPV(cpvstr)
        if c.fullver is None:
            cpvstr = cpvstr + "-" + pkg.fullver

        metadata.package.__init__(self, parent_repository, cpvstr)
        sfunc = object.__setattr__
        sfunc(self, "data", IndeterminantDict(lambda *a: str(), data))
        sfunc(self, "_orig_data", data)
        sfunc(self, "actual_pkg", pkg)

    def __getattr__(self, attr):
        if attr in self._orig_data:
            return self._orig_data[attr]
        return metadata.package.__getattr__(self, attr)

    _get_attr = package._get_attr.copy()
