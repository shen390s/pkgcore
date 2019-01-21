# Copyright: 2019 Tim Harder <radhermit@gmail.com>
# License: BSD/GPL2

"""display package keywords"""

import os

from snakeoil.strings import pluralism as _pl

from pkgcore.ebuild import restricts
from pkgcore.util import commandline, packages as pkgutils
from pkgcore.util.tabulate import tabulate, tabulate_formats
from pkgcore.repository import errors as repo_errors


argparser = commandline.ArgumentParser(description=__doc__, script=(__file__, __name__))
argparser.add_argument(
    'targets', metavar='target', nargs='*',
    action=commandline.StoreTarget,
    help='extended atom matching of packages')

output_opts = argparser.add_argument_group('output options')
output_opts.add_argument(
    '-f', '--format', default='pshowkw', metavar='FORMAT',
    choices=tabulate_formats,
    help='keywords table format',
    docs=f"""
        Output table using specified tabular format (defaults to compressed,
        custom pshowkw format).

        Available formats: {', '.join(tabulate_formats)}
    """)
output_opts.add_argument(
    '-c', '--collapse', action='store_true',
    help='show collapsed list of arches')

arch_options = argparser.add_argument_group('arch options')
arch_options.add_argument(
    '-s', '--stable', action='store_true',
    help='show stable arches')
arch_options.add_argument(
    '-u', '--unstable', action='store_true',
    help='show unstable arches')
arch_options.add_argument(
    '-o', '--only-unstable', action='store_true',
    help='show arches that only have unstable keywords')
arch_options.add_argument(
    '-p', '--prefix', action='store_true',
    help='show prefix and non-native arches')
arch_options.add_argument(
    '-a', '--arch', action='csv_negations',
    help='select arches to display')

# TODO: allow multi-repo comma-separated input
target_opts = argparser.add_argument_group('target options')
target_opts.add_argument(
    '-r', '--repo', dest='selected_repo', metavar='REPO', priority=29,
    action=commandline.StoreRepoObject,
    repo_type='all-raw', allow_external_repos=True,
    help='repo to query (defaults to all ebuild repos)')
@argparser.bind_delayed_default(30, 'repos')
def _setup_repos(namespace, attr):
    repo = namespace.selected_repo
    namespace.cwd = os.getcwd()

    # TODO: move this to StoreRepoObject
    if repo is None:
        repo = namespace.domain.all_ebuild_repos_raw
        # try to add the current working directory as an external repo
        if namespace.cwd not in repo and not namespace.targets:
            path = namespace.cwd
            while path != namespace.domain.root:
                try:
                    repo = namespace.domain.add_repo(path, config=namespace.config)
                    break
                except repo_errors.InvalidRepo:
                    path = os.path.dirname(path)

    namespace.repo = repo


@argparser.bind_delayed_default(40, 'arches')
def _setup_arches(namespace, attr):
    default_repo = namespace.config.get_default('repo')

    try:
        known_arches = {arch for r in namespace.repo.trees
                        for arch in r.config.known_arches}
    except AttributeError:
        try:
            # binary/vdb repos use known arches from the default repo
            known_arches = default_repo.config.known_arches
        except AttributeError:
            # TODO: remove fallback for tests after fixing default repo pull
            # from faked config
            known_arches = set()

    arches = known_arches
    if namespace.arch is not None:
        disabled_arches, enabled_arches = namespace.arch
        disabled_arches = set(disabled_arches)
        enabled_arches = set(enabled_arches)
        unknown_arches = disabled_arches.difference(known_arches) | enabled_arches.difference(known_arches)
        if unknown_arches:
            unknown = ', '.join(map(repr, sorted(unknown_arches)))
            known = ', '.join(sorted(known_arches))
            plural = _pl(unknown_arches, plural='es')
            argparser.error(f'unknown arch{plural}: {unknown} (choices: {known})')
        if enabled_arches:
            arches = arches.intersection(enabled_arches)
        if disabled_arches:
            arches = arches - disabled_arches

    prefix_arches = set(x for x in arches if '-' in x)
    native_arches = arches.difference(prefix_arches)
    arches = native_arches
    if namespace.prefix:
        arches = arches.union(prefix_arches)
    if namespace.stable:
        try:
            stable_arches = {arch for r in namespace.repo.trees
                             for arch in r.config.profiles.arches('stable')}
        except AttributeError:
            # binary/vdb repos use stable arches from the default repo
            stable_arches = default_repo.config.profiles.arches('stable')
        arches = arches.intersection(stable_arches)

    namespace.known_arches = known_arches
    namespace.prefix_arches = prefix_arches
    namespace.native_arches = native_arches
    namespace.arches = arches


@argparser.bind_final_check
def _validate_args(parser, namespace):
    namespace.pkg_dir = False

    if not namespace.targets:
        if namespace.selected_repo:
            # use repo restriction since no targets specified
            restriction = restricts.RepositoryDep(namespace.selected_repo.repo_id)
            token = namespace.selected_repo.repo_id
        else:
            # Use a path restriction if we're in a repo, obviously it'll work
            # faster if we're in an invididual ebuild dir but we're not that
            # restrictive.
            try:
                restriction = namespace.repo.path_restrict(namespace.cwd)
                token = namespace.cwd
            except (AttributeError, ValueError):
                parser.error('missing target argument and not in a supported repo')

            # determine if we're grabbing the keywords for a single pkg in cwd
            namespace.pkg_dir = any(
                isinstance(x, restricts.PackageDep)
                for x in reversed(restriction.restrictions))

        namespace.targets = [(token, restriction)]


def _collapse_arches(options, pkgs):
    """Collapse arches into a single set."""
    keywords = set()
    stable_keywords = set()
    unstable_keywords = set()
    for pkg in pkgs:
        for x in pkg.keywords:
            if x[0] == '~':
                unstable_keywords.add(x[1:])
            elif x in options.arches:
                stable_keywords.add(x)
    if options.unstable:
        keywords.update(unstable_keywords)
    if options.only_unstable:
        keywords.update(unstable_keywords.difference(stable_keywords))
    if not keywords or options.stable:
        keywords.update(stable_keywords)
    return (
        sorted(keywords.intersection(options.native_arches)) +
        sorted(keywords.intersection(options.prefix_arches)))


def _render_rows(options, pkgs, arches):
    """Build rows for tabular data output."""
    for pkg in sorted(pkgs):
        keywords = set(pkg.keywords)
        row = [pkg.fullver]
        for arch in arches:
            if arch in keywords:
                row.append('+')
            elif f'~{arch}' in keywords:
                row.append('~')
            elif f'-{arch}' in keywords:
                row.append('-')
            elif '-*' in keywords:
                row.append('*')
            else:
                row.append('o')
        row.extend([pkg.eapi, pkg.fullslot, pkg.repo.repo_id])
        yield row


@argparser.bind_main_func
def main(options, out, err):
    continued = False
    for token, restriction in options.targets:
        for pkgs in pkgutils.groupby_pkg(options.repo.itermatch(restriction, sorter=sorted)):
            if options.collapse:
                out.write(' '.join(_collapse_arches(options, pkgs)))
            else:
                arches = sorted(options.arches.intersection(options.native_arches))
                if options.prefix:
                    arches += sorted(options.arches.intersection(options.prefix_arches))
                headers = [''] + arches + ['eapi', 'slot', 'repo']
                if continued:
                    out.write()
                if not options.pkg_dir:
                    pkgs = list(pkgs)
                    out.write(f'keywords for {pkgs[0].unversioned_atom}:')
                data = _render_rows(options, pkgs, arches)
                table = tabulate(data, headers=headers, tablefmt=options.format)
                out.write(table)
            continued = True

    if not continued:
        err.write(f"{options.prog}: no matches for {token!r}")
        return 1