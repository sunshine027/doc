#!/usr/bin/env python
from __future__ import print_function
import os
import sys
import string
import logging
import argparse
import itertools
from collections import defaultdict
from subprocess import check_output, Popen, PIPE
from six.moves.urllib.parse import urlsplit
from six.moves import input
try:
    from string import lowercase as ascii_lowercase
except ImportError:
    from string import ascii_lowercase


DEBUG = False

logger = logging.getLogger(os.path.basename(__file__))


def call(*args, **kw):
    quiet = False
    if 'quiet' in kw:
        quiet = kw.pop('quiet')

    if not quiet:
        cmd = kw.get("args") 
        if cmd is None: 
            cmd = args[0] 

        cmd = ' '.join(cmd)
        logger.debug(cmd)

    if DEBUG:
        return 0

    return check_output(*args, **kw).decode('utf8')


def get_stdout(*args, **kw):
    proc = Popen(*args, stdout=PIPE, **kw)
    return proc.communicate()[0]


def guess_git_root():
    paths = os.getcwd().split(os.sep)
    while paths:
        git = os.path.join(os.sep.join(paths), '.git')
        if not git.startswith(os.sep):
            break
        if os.path.isdir(git):
            return git[:-len('/.git')]
        paths.pop()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument('-r', '--reviewer', nargs='*',
                        help='add to remote.origin.receivepack')
    return parser.parse_args()


def download_commit_msg_hook():
    target = os.path.join(guess_git_root(), '.git', 'hooks', 'commit-msg')
    if os.path.exists(target):
        logger.info('hook exists, skip')
        return

    cmd_get_remote_url = ['git', 'config', '--local', 'remote.origin.url']

    url = call(cmd_get_remote_url, quiet=1)
    if '://' in url:
        parts = urlsplit(url)
        netloc = parts.netloc.split(':')
        if len(netloc) > 1:
            host, port = netloc
        else:
            host, port = netloc[0], None
    else:
        parts = url.split(':')
        host = parts[0]
        if len(parts) > 2:
            port = parts[1]
        else:
            port = None
        
    source = '%s:hooks/commit-msg' % host

    cmd_scp_commit_hook = ['scp']
    if port:
        cmd_scp_commit_hook.extend(['-P', port])
    cmd_scp_commit_hook.extend([source, target])
    call(cmd_scp_commit_hook)


def set_default_push_branch():
    cmd_get_push = ['git', 'config', 'remote.origin.push']
    val = get_stdout(cmd_get_push).strip()
    if val:
        logger.info('default push branch exists, skip')
        return

    cmd_set_push = ['git', 'config', '--local', 'remote.origin.push', 'HEAD:refs/for/devel']
    call(cmd_set_push)


def set_reviewers(args):
    if args.reviewer:
        reviewers = args.reviewer
    else:
        cmd_get_reviewers = ['git', 'config', 'remote.origin.receivepack']
        val = str(get_stdout(cmd_get_reviewers).strip())
        if val:
            existing = [ opt[len('--reviewer='):]
                for opt in val.strip().split()
                if opt.startswith('--reviewer=') ]
            logger.info('Existing reviewers:\n--\n%s\n--', '\n'.join(existing))
            sel = input('Do you want to add more reviewers ? [N/y] ').strip()
            if sel.lower() not in ('y', 'yes'):
                return
            reviewers = existing + input_reviewers()
        else:
            sel = input('Do you want to input reviewers interactively ?[N/y] ').strip()
            if sel.lower() not in ('y', 'yes'):
                return
            reviewers = input_reviewers()

    val = 'git receive-pack {}'.format(
        ' '.join([ '--reviewer=%s' % i for i in set(reviewers) ]))

    cmd_set_reviewer = ['git', 'config', '--local', 'remote.origin.receivepack', val]
    call(cmd_set_reviewer)


def guess_keywords_from_email(email):
    TOO_SIMPLE = [ i for i in ascii_lowercase ]
    name = email.split('@', 1)[0].lower()
    parts = [i for i in name.replace('-', ' ').replace('.', ' ').split() \
                 if i not in TOO_SIMPLE ]
    for n in range(len(parts)):
        for perm in itertools.permutations(parts, n+1):
            if len(perm) == 1:
                yield perm[0]
            else:
                yield ''.join(perm)
                yield ' '.join(perm)
                yield ''.join([ i[0] for i in perm ])


class NameCache(object):

    def __init__(self):
        self.index = defaultdict(list)
        self.no_cache = []

    def get(self, s):
        ret = []
        for kw, emails in self.index.items():
            if kw.startswith(s):
                ret.extend(emails)

        if not ret:
            self.no_cache.append(s)
        return list(set(ret))

    @classmethod
    def load(cls, fp):
        cache = NameCache()
        lines = [j for j in \
                     set([ i.rstrip() for i in fp.readlines() ])
                     if j and not j.startswith('#') ]

        values = set()
        for line in lines:
            for kw in guess_keywords_from_email(line):
                cache.index[kw].append(line)
                values.add(line)

        logger.info('emails loaded:\n--\n%s\n--', '\n'.join(sorted(values)))
        return cache

    def dump(self, fp):
        values = self.no_cache
        for val in self.index.values():
            values.extend(val)
        buf = '\n'.join(set(values))
        fp.write(buf)


def input_reviewers():
    def choose_from_multi(items):
        print('multi names found, please choose some or all of them. Accepted input format examples:')
        print('2 or 1,3,4 or a or A')
        for i, item in enumerate(items):
            print('[{}] {}'.format(i+1, item))
        print('[a] all of them')

        sel = input('? ').strip().lower()
        if sel in ('a', 'all'):
            return items

        seli = []
        for i in sel.replace(',', ' ').split():
            try:
                j = int(i) - 1
            except ValueError:
                pass
            else:
                if j >= 0 and j < len(items):
                    seli.append(j)

        return [ items[i] for i in seli ]


    cache_filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'email.cache')
    if os.path.exists(cache_filename):
        cache = NameCache.load(open(cache_filename))
    else:
        cache = NameCache()

    names = []
    while 1:
        try:
            line = input('please input reviewers(name or email): ')
        except (KeyboardInterrupt, EOFError):
            break
        else:
            line = line.strip()
            if not line or line.lower() in ('q', 'exit', 'quit'):
                break

        items = cache.get(line)
        if not items:
            names.append(line)
        elif len(items) == 1:
            logger.info('adding %s', items[0])
            names.append(items[0])
        else:
            multi = choose_from_multi(items)
            for m in multi:
                logger.info('adding %s', m)
            names.extend(multi)

    with open(cache_filename, 'w') as fp:
        print(cache_filename)
        cache.dump(fp)

    return names


def main(args):
    set_default_push_branch()
    set_reviewers(args)
    download_commit_msg_hook()


if __name__ == '__main__':
    args = parse_args()

    DEBUG = args.debug
    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

    main(args)
