"""
This module contains:
  * all low-level code for extracting, activating and deactivating packages
  * a very simple CLI

These API functions  have argument names referring to:

    dist:        canonical package name (e.g. 'numpy-1.6.2-py26_0')

    pkgs_dir:    the "packages directory" (e.g. '/opt/anaconda/pkgs')

    prefix:      the prefix of a particular environment, which may also
                 be the "default" environment (i.e. sys.prefix),
                 but is otherwise something like '/opt/anaconda/envs/foo',
                 or even any prefix, e.g. '/home/joe/myenv'

Also, this module is directly invoked by the (self extracting (sfx)) tarball
installer to create the initial environment, therefore it needs to be
standalone, i.e. not import any other parts of conda (only depend of
the standard library).
"""

import os
import json
import shutil
import stat
import sys
import tarfile
import logging
from os.path import isdir, isfile, islink, join


log = logging.getLogger(__name__)


can_hard_link = bool(sys.platform != 'win32')


def rm_rf(path):
    if islink(path) or isfile(path):
        # Note that we have to check if the destination is a link because
        # exists('/path/to/dead-link') will return False, although
        # islink('/path/to/dead-link') is True.
        os.unlink(path)

    elif isdir(path):
        shutil.rmtree(path)


def yield_lines(path):
    for line in open(path):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        yield line


def update_prefix(path, new_prefix):
    with open(path) as fi:
        data = fi.read()
    new_data = data.replace('/opt/anaconda1anaconda2'
                            # the build prefix is intentionally split into
                            # parts, such that running this program on itself
                            # will leave it unchanged
                            'anaconda3', new_prefix)
    if new_data == data:
        return
    st = os.stat(path)
    os.unlink(path)
    with open(path, 'w') as fo:
        fo.write(new_data)
    os.chmod(path, stat.S_IMODE(st.st_mode))


def create_meta(prefix, dist, info_dir, files):
    """
    Create the conda metadata, in a given prefix, for a given package.
    """
    meta_dir = join(prefix, 'conda-meta')
    with open(join(info_dir, 'index.json')) as fi:
        meta = json.load(fi)
    meta['files'] = files
    if not isdir(meta_dir):
        os.mkdir(meta_dir)
    with open(join(meta_dir, dist + '.json'), 'w') as fo:
        json.dump(meta, fo, indent=2, sort_keys=True)


# ========================== begin API functions =========================

def activated(prefix):
    """
    Return the (set of canonical names) of activated packages in prefix.
    """
    meta_dir = join(prefix, 'conda-meta')
    if not isdir(meta_dir):
        return set()
    return set(fn[:-5] for fn in os.listdir(meta_dir) if fn.endswith('.json'))


def get_meta(dist, prefix):
    """
    Return the install meta-data for an active package in a prefix, or None
    if the package is not active in the prefix.
    """
    meta_path = join(prefix, 'conda-meta', dist + '.json')
    try:
        with open(meta_path) as fi:
            return json.load(fi)
    except OSError:
        return None


def extracted(pkgs_dir):
    """
    Return, the set of canonical names of, all extracted (available) packages.
    """
    if can_hard_link:
        return set(fn for fn in os.listdir(pkgs_dir)
                   if isdir(join(pkgs_dir, fn)))
    else:
        return set(fn[:-8] for fn in os.listdir(pkgs_dir)
                   if fn.endswith('.tar.bz2'))


def extract(pkgs_dir, dist, cleanup=False):
    '''
    Extract a package tarball into the conda packages directory, making
    it available.  We assume that the compressed packages is located in the
    packages directory.
    '''
    bz2path = join(pkgs_dir, dist + '.tar.bz2')
    assert isfile(bz2path), bz2path
    if not can_hard_link:
        return
    t = tarfile.open(bz2path)
    t.extractall(path=join(pkgs_dir, dist))
    t.close()
    if cleanup:
        os.unlink(bz2path)


def remove(pkgs_dir, dist):
    '''
    Remove a package from the packages directory.
    '''
    bz2path = join(pkgs_dir, dist + '.tar.bz2')
    rm_rf(bz2path)
    if can_hard_link:
        rm_rf(join(pkgs_dir, dist))


def activate(pkgs_dir, dist, prefix):
    '''
    Set up a packages in a specified (environment) prefix.  We assume that
    the packages has been extracted (using extract() above).
    '''
    if can_hard_link:
        dist_dir = join(pkgs_dir, dist)
        info_dir = join(dist_dir, 'info')
        files = list(yield_lines(join(info_dir, 'files')))
        for f in files:
            src = join(dist_dir, f)
            fdn, fbn = os.path.split(f)
            dst_dir = join(prefix, fdn)
            if not isdir(dst_dir):
                os.makedirs(dst_dir)
            dst = join(dst_dir, fbn)
            if os.path.exists(dst):
                log.warn("file already exists: '%s'" % dst)
            else:
                try:
                    os.link(src, dst)
                except OSError:
                    log.error('failed to link (src=%r, dst=%r)' % (src, dst))

    else: # cannot hard link files
        info_dir = join(prefix, 'info')
        rm_rf(info_dir)
        t = tarfile.open(join(pkgs_dir, dist + '.tar.bz2'))
        t.extractall(path=prefix)
        t.close()
        files = list(yield_lines(join(info_dir, 'files')))

    for f in yield_lines(join(info_dir, 'has_prefix')):
        update_prefix(join(prefix, f), prefix)

    create_meta(prefix, dist, info_dir, files)


def deactivate(dist, prefix):
    '''
    Remove a package from the specified environment, it is an error of the
    package does not exist in the prefix.
    '''
    meta_path = join(prefix, 'conda-meta', dist + '.json')
    with open(meta_path) as fi:
        meta = json.load(fi)

    dst_dirs = set()
    for f in meta['files']:
        fdn, fbn = os.path.split(f)
        dst_dir = join(prefix, fdn)
        dst_dirs.add(dst_dir)
        dst = join(dst_dir, fbn)
        try:
            os.unlink(dst)
        except OSError: # file might not exist
            log.debug("could not remove file: '%s'" % dst)

    for path in sorted(dst_dirs, key=len, reverse=True):
        try:
            os.rmdir(path)
        except OSError: # directory might not exist or not be empty
            log.debug("could not remove directory: '%s'" % dst)

    os.unlink(meta_path)

# =========================== end API functions ==========================

def main():
    from pprint import pprint
    from optparse import OptionParser

    p = OptionParser(
        usage="usage: %prog [options] [TARBALL/NAME]",
        description="low-level conda install tool, by default extracts "
                    "(if necessary) and activates a TARBALL")

    p.add_option('-l', '--list',
                 action="store_true",
                 help="list all activated packages")

    p.add_option('--list-extracted',
                 action="store_true",
                 help="list all extracted packages")

    p.add_option('-m', '--meta',
                 action="store_true",
                 help="display the mata-data of a package")

    p.add_option('-e', '--extract',
                 action="store_true",
                 help="extract a package")

    p.add_option('-a', '--activate',
                 action="store_true",
                 help="activate a package")

    p.add_option('-d', '--deactivate',
                 action="store_true",
                 help="deactivate a package")

    p.add_option('-r', '--remove',
                 action="store_true",
                 help="remove a package (from being available)")

    p.add_option('-p', '--prefix',
                 action="store",
                 default=sys.prefix,
                 help="prefix (defulats to %default)")

    p.add_option('--pkgs-dir',
                 action="store",
                 default=join(sys.prefix, 'pkgs'),
                 help="packages directory (defulats to %default)")

    p.add_option('--activate-all',
                 action="store_true",
                 help="activate all extracted packages")

    p.add_option('-v', '--verbose',
                 action="store_true")

    opts, args = p.parse_args()

    if opts.list or opts.list_extracted or opts.activate_all:
        if args:
            p.error('no arguments expected')
    else:
        if len(args) == 1:
            dist = args[0]
            if dist.endswith('.tar.bz2'):
                dist = dist[:-8]
        else:
            p.error('exactly one argument expected')

    if opts.verbose:
        print "pkgs_dir: %r" % opts.pkgs_dir
        print "prefix  : %r" % opts.prefix

    if opts.list:
        pprint(sorted(activated(opts.prefix)))
        return

    if opts.list_extracted:
        pprint(sorted(extracted(opts.pkgs_dir)))
        return

    if opts.meta:
        meta = get_meta(dist, opts.prefix)
        pprint(meta)
        return

    if opts.activate_all:
        for d in sorted(extracted(opts.pkgs_dir)):
            activate(opts.pkgs_dir, d, opts.prefix)
        return

    do_steps = not (opts.remove or opts.extract or opts.activate or
                    opts.deactivate)
    if opts.verbose:
        print "do_steps: %r" % do_steps

    if do_steps:
        path = args[0]
        if not (path.endswith('.tar.bz2') and isfile(path)):
            p.error('path .tar.bz2 package expected')

    if do_steps or opts.remove:
        if opts.verbose:
            print "removing: %r" % dist
        remove(opts.pkgs_dir, dist)

    if do_steps:
        shutil.copyfile(path, join(opts.pkgs_dir, dist + '.tar.bz2'))

    if do_steps or opts.extract:
        if opts.verbose:
            print "extracting: %r" % dist
        extract(opts.pkgs_dir, dist)

    if (do_steps and dist in activated(opts.prefix)) or opts.deactivate:
        if opts.verbose:
            print "deactivating: %r" % dist
        deactivate(dist, opts.prefix)

    if do_steps or opts.activate:
        if opts.verbose:
            print "activating: %r" % dist
        activate(opts.pkgs_dir, dist, opts.prefix)


if __name__ == '__main__':
    main()
