# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import absolute_import, print_function, unicode_literals

"""Functionality to support VCS syncing for WPT."""

import logging
import os
import re
import subprocess
import sys
import shutil
import uuid

from ConfigParser import (
    RawConfigParser,
)

from . import pulse
from .gitutil import GitCommand


rev_re = re.compile("revision=(?P<rev>[0-9a-f]{40})")
logger = logging.getLogger('mozvcssync.servo')


def run_pulse_listener(c):
    """Trigger events from Pulse messages."""
    consumer = pulse.get_consumer(userid=c['pulse_userid'],
                                  password=c['pulse_password'],
                                  hostname=c['pulse_host'],
                                  port=c['pulse_port'],
                                  ssl=c['pulse_ssl'],
                                  github_exchange=c['pulse_github_exchange'],
                                  github_queue=c['pulse_github_queue'],
                                  github_routing_key=c['pulse_github_routing_key'])

    def on_github_message(body, message):
        wpt_dir = os.path.join('testing', 'web-platform', 'tests')
        logger.warn('Look, an event:' + body['event'])
        logger.warn('from:' + body['_meta']['routing_key'])
        pr_id = body['payload']['pull_request']['number']
        # assuming:
        # - git cinnabar, checkout of the gecko repo,
        #     remotes configured, mercurial python lib
        get_pr(c['wpt_source_url'], c['path_to_wpt'], pr_id)
        gecko_pr_branch = create_fresh_branch(c['path_to_gecko'])
        copy_changes(c['path_to_wpt'],
                     os.path.join(c['path_to_gecko'], wpt_dir))
        _mach('wpt-manifest-update', c['path_to_gecko'])
        is_changed = commit_changes(c['path_to_gecko'], wpt_dir
                                    "PR " + pr_id)
        if is_changed:
            push_to_try(c['path_to_gecko'], gecko_pr_branch)
        message.ack()

    consumer.github_callbacks.append(on_github_message)

    try:
        with consumer:
            consumer.listen_forever()
    except KeyboardInterrupt:
        pass


def load_config(path):
    c = RawConfigParser()
    c.read(path)
    wpt = 'web-platform-tests'

    d = {}
    d.update(c.items(wpt))

    d['pulse_port'] = c.getint(wpt, 'pulse_port')
    d['pulse_ssl'] = c.getboolean(wpt, 'pulse_ssl')

    return d


def configure_stdout():
    # Unbuffer stdout.
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 1)

    # Log to stdout.
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(name)s %(message)s')
    handler.setFormatter(formatter)
    root.addHandler(handler)


def pulse_daemon():
    import argparse

    configure_stdout()

    parser = argparse.ArgumentParser()
    parser.add_argument('config', help='Path to config file to load')

    args = parser.parse_args()

    config = load_config(args.config)
    run_pulse_listener(config)


def get_pr(git_source_url, git_repo_path, pr_id, ref='master'):
    """ Pull shallow repo and checkout given pr """
    git = GitCommand(os.path.abspath(git_repo_path))
    if not os.path.exists(git_repo_path):
        git.cmd(b'init', git_repo_path)

    git.cmd(b'checkout', b'master')
    git.cmd(b'clean', b'-xdf')
    git.cmd(b'pull', b'--no-tags', b'--ff-only', git_source_url,
            b'heads/%s:heads/%s' % (ref, ref))
    git.cmd(b'fetch', b'--no-tags', git_source_url,
            b'pull/%s/head:heads/pull_%s' % (pr_id, pr_id))
    git.cmd(b'checkout', b'pull_%s' % pr_id)
    git.cmd(b'merge', 'heads/%s' % ref)


def create_fresh_branch(git_repo_path, base="central", tip="central/branches/default/tip"):
    """reset repo and checkout a new branch for the new changes"""
    git = GitCommand(os.path.abspath(git_repo_path))
    git.cmd(b'checkout', base)
    git.cmd(b'pull')
    git.cmd(b'checkout', tip)
    branch = uuid.uuid4().hex
    git.cmd(b'checkout', b'-b', branch)

    return branch


def copy_changes(source, dest, ignore=None):
    # TODO instead of copying files or convertin/moving patches, find
    # a way to apply changesets from one repo to the other (git subtree,
    # read-tree, filter-branch?)
    source = os.path.abspath(source)
    dest = os.path.abspath(dest)
    if ignore is None:
        ignore = ['.git', 'css']

    if os.path.exists(dest):
        assert os.path.isdir(dest)
        shutil.rmtree(dest)

    def ignore_in_path(ignore_path, *patterns):
        patterns_fn = shutil.ignore_patterns(*patterns)

        def ignore(path, names):
            if path == ignore_path:
                return patterns_fn(path, names)
            return []
        return ignore

    shutil.copytree(source, dest, ignore=ignore_in_path(source, *ignore))


def commit_changes(git_repo_path, path, message):
    git = GitCommand(os.path.abspath(git_repo_path))
    # TODO nice commit message
    git.cmd(b'add', b'-A', path)
    if not git.get(b'diff', b'--cached', b'--name-only'):
        logger.info("Nothing to commit")
    git.cmd(b'commit', b'-m', message)
    return True


def push_to_try(git_repo_path, branch):
    # TODO determine affected tests
    # push with --try-test-paths web-platform-tests:path/to/dir ...
    # -u web-platform-tests-1
    results_url = None
    git = GitCommand(os.path.abspath(git_repo_path))
    try_message = "try: -b o -p linux64 -u web-platform-tests-1 -t none"
    git.cmd(b'checkout', branch)
    git.cmd(b'commit', b'--allow-empty', b'-m', try_message)
    try:
        output = git.get(b'push', b'try', stderr=subprocess.STDOUT)
        rev_match = rev_re.search(output)
        results_url = ("https://treeherder.mozilla.org/#/"
                       "jobs?repo=try&revision=") + rev_match.group('rev')
    finally:
        git.cmd(b'reset', b'HEAD~')

    return results_url


def _mach(name, path_to_gecko, options=None):
    command = [os.path.join(path_to_gecko, b"mach"), name]
    if options:
        command.extend(options)
    subprocess.check_call(command, cwd=path_to_gecko)


