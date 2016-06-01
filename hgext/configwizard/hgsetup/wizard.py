# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this,
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import unicode_literals

import difflib
import errno
import os
import shutil
import ssl
import stat
import sys
import subprocess

from distutils.version import LooseVersion

from configobj import ConfigObjError
from StringIO import StringIO

from mozversioncontrol import get_hg_path, get_hg_version

from .update import MercurialUpdater
from .config import (
    config_file,
    MercurialConfig,
    ParseException,
)


BZEXPORT_INFO = '''
If you plan on uploading patches to Mozilla, there is an extension called
bzexport that makes it easy to upload patches from the command line via the
|hg bzexport| command. More info is available at
https://hg.mozilla.org/hgcustom/version-control-tools/file/default/hgext/bzexport/README

(Relevant config option: extensions.bzexport)

Would you like to activate bzexport
'''.strip()

FINISHED = '''
Your Mercurial should now be properly configured and recommended extensions
should be up to date!
'''.strip()

REVIEWBOARD_MINIMUM_VERSION = LooseVersion('3.5')

REVIEWBOARD_INCOMPATIBLE = '''
Your Mercurial is too old to use the reviewboard extension, which is necessary
to conduct code review.

Please upgrade to Mercurial %s or newer to use this extension.
'''.strip()

MISSING_BUGZILLA_CREDENTIALS = '''
You do not have your Bugzilla API Key defined in your Mercurial config.

Various extensions make use of a Bugzilla API Key to interface with
Bugzilla to enrich your development experience.

The Bugzilla API Key is optional. If you do not provide one, associated
functionality will not be enabled, we will attempt to find a Bugzilla cookie
from a Firefox profile, or you will be prompted for your Bugzilla credentials
when they are needed.

You should only need to configure a Bugzilla API Key once.
'''.lstrip()

BUGZILLA_API_KEY_INSTRUCTIONS = '''
Bugzilla API Keys can only be obtained through the Bugzilla web interface.

Please perform the following steps:

  1) Open https://bugzilla.mozilla.org/userprefs.cgi?tab=apikey
  2) Generate a new API Key
  3) Copy the generated key and paste it here
'''.lstrip()

LEGACY_BUGZILLA_CREDENTIALS_DETECTED = '''
Your existing Mercurial config uses a legacy method for defining Bugzilla
credentials. Bugzilla API Keys are the most secure and preferred method
for defining Bugzilla credentials. Bugzilla API Keys are also required
if you have enabled 2 Factor Authentication in Bugzilla.

All consumers formerly looking at these options should support API Keys.
'''.lstrip()

BZPOST_MINIMUM_VERSION = LooseVersion('3.5')

BZPOST_INFO = '''
The bzpost extension automatically records the URLs of pushed commits to
referenced Bugzilla bugs after push.

(Relevant config option: extensions.bzpost)

Would you like to activate bzpost
'''.strip()

FIREFOXTREE_MINIMUM_VERSION = LooseVersion('3.5')

FIREFOXTREE_INFO = '''
The firefoxtree extension makes interacting with the multiple Firefox
repositories easier:

* Aliases for common trees are pre-defined. e.g. `hg pull central`
* Pulling from known Firefox trees will create "remote refs" appearing as
  tags. e.g. pulling from fx-team will produce a "fx-team" tag.
* The `hg fxheads` command will list the heads of all pulled Firefox repos
  for easy reference.
* `hg push` will limit itself to pushing a single head when pushing to
  Firefox repos.
* A pre-push hook will prevent you from pushing multiple heads to known
  Firefox repos. This acts quicker than a server-side hook.

The firefoxtree extension is *strongly* recommended if you:

a) aggregate multiple Firefox repositories into a single local repo
b) perform head/bookmark-based development (as opposed to mq)

(Relevant config option: extensions.firefoxtree)

Would you like to activate firefoxtree
'''.strip()

PUSHTOTRY_MINIMUM_VERSION = LooseVersion('3.5')

PUSHTOTRY_INFO = '''
The push-to-try extension generates a temporary commit with a given
try syntax and pushes it to the try server. The extension is intended
to be used in concert with other tools generating try syntax so that
they can push to try without depending on mq or other workarounds.

(Relevant config option: extensions.push-to-try)

Would you like to activate push-to-try
'''.strip()

WIP_INFO = '''
It is common to want a quick view of changesets that are in progress.

The ``hg wip`` command provides should a view.

Example Usage:

  $ hg wip
  o   4084:fcfa34d0387b dminor  @
  |  mozreview: use repository name when displaying treeherder results (bug 1230548) r=mcote
  | @   4083:786baf6d476a gps
  | |  mozreview: create child review requests from batch API
  | o   4082:3f100fa4a94f gps
  | |  mozreview: copy more read-only processing code; r?smacleod
  | o   4081:939417680cbe gps
  |/   mozreview: add web API to submit an entire series of commits (bug 1229468); r?smacleod

(Not shown are the colors that help denote the state each changeset
is in.)

(Relevant config options: alias.wip, revsetalias.wip, templates.wip)

Would you like to install the `hg wip` alias?
'''.strip()

HGWATCHMAN_MINIMUM_VERSION = LooseVersion('3.5.2')

HGWATCHMAN_INFO = '''
The hgwatchman extension integrates the watchman filesystem watching
tool with Mercurial. Commands like `hg status`, `hg diff`, and
`hg commit` that need to examine filesystem state can query watchman
and obtain filesystem state nearly instantaneously. The result is much
faster command execution.

When installed, the hgwatchman extension will launch a background
watchman file watching daemon for accessed Mercurial repositories. It
should "just work."

Would you like to install hgwatchman
'''.strip()

FILE_PERMISSIONS_WARNING = '''
Your hgrc file is currently readable by others.

Sensitive information such as your Bugzilla credentials could be
stolen if others have access to this file/machine.
'''.strip()

MULTIPLE_VCT = '''
*** WARNING ***

Multiple version-control-tools repositories are referenced in your
Mercurial config. Extensions and other code within the
version-control-tools repository could run with inconsistent results.

Please manually edit the following file to reference a single
version-control-tools repository:

    %s
'''.lstrip()


class MercurialSetupWizard(object):
    """Command-line wizard to help users configure Mercurial."""

    def __init__(self, state_dir):
        # We use normpath since Mercurial expects the hgrc to use native path
        # separators, but state_dir uses unix style paths even on Windows.
        self.state_dir = os.path.normpath(state_dir)
        self.ext_dir = os.path.join(self.state_dir, 'mercurial', 'extensions')
        self.vcs_tools_dir = os.path.join(self.state_dir, 'version-control-tools')
        self.updater = MercurialUpdater(state_dir)

    def run(self, config_paths):
        try:
            os.makedirs(self.ext_dir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

        hg = get_hg_path()
        config_path = config_file(config_paths)

        self.updater.update_all()

        hg_version = get_hg_version(hg)

        # hgwatchman is provided by MozillaBuild and we don't yet support
        # Linux/BSD.
        if ('hgwatchman' not in c.extensions
            and sys.platform.startswith('darwin')
            and hg_version >= HGWATCHMAN_MINIMUM_VERSION
            and self._prompt_yn(HGWATCHMAN_INFO)):
            # Unlike other extensions, we need to run an installer
            # to compile a Python C extension.
            try:
                subprocess.check_output(
                    ['make', 'local'],
                    cwd=self.updater.hgwatchman_dir,
                    stderr=subprocess.STDOUT)

                ext_path = os.path.join(self.updater.hgwatchman_dir,
                                        'hgwatchman')
                if self.can_use_extension(c, 'hgwatchman', ext_path):
                    c.activate_extension('hgwatchman', ext_path)
            except subprocess.CalledProcessError as e:
                print('Error compiling hgwatchman; will not install hgwatchman')
                print(e.output)

        if 'reviewboard' not in c.extensions:
            if hg_version < REVIEWBOARD_MINIMUM_VERSION:
                print(REVIEWBOARD_INCOMPATIBLE % REVIEWBOARD_MINIMUM_VERSION)
            else:
                p = os.path.join(self.vcs_tools_dir, 'hgext', 'reviewboard',
                    'client.py')
                self.prompt_external_extension(c, 'reviewboard',
                    'Would you like to enable the reviewboard extension so '
                    'you can easily initiate code reviews against Mozilla '
                    'projects',
                    path=p)

        self.prompt_external_extension(c, 'bzexport', BZEXPORT_INFO)

        if hg_version >= BZPOST_MINIMUM_VERSION:
            self.prompt_external_extension(c, 'bzpost', BZPOST_INFO)

        if hg_version >= FIREFOXTREE_MINIMUM_VERSION:
            self.prompt_external_extension(c, 'firefoxtree', FIREFOXTREE_INFO)

        if hg_version >= PUSHTOTRY_MINIMUM_VERSION:
            self.prompt_external_extension(c, 'push-to-try', PUSHTOTRY_INFO)

        if not c.have_wip():
            if self._prompt_yn(WIP_INFO):
                c.install_wip_alias()

        if 'reviewboard' in c.extensions or 'bzpost' in c.extensions:
            bzuser, bzpass, bzuserid, bzcookie, bzapikey = c.get_bugzilla_credentials()

            if not bzuser or not bzapikey:
                print(MISSING_BUGZILLA_CREDENTIALS)

            if not bzuser:
                bzuser = self._prompt('What is your Bugzilla email address? (optional)',
                    allow_empty=True)

            if bzuser and not bzapikey:
                print(BUGZILLA_API_KEY_INSTRUCTIONS)
                bzapikey = self._prompt('Please enter a Bugzilla API Key: (optional)',
                    allow_empty=True)

            if bzuser or bzapikey:
                c.set_bugzilla_credentials(bzuser, bzapikey)

            if bzpass or bzuserid or bzcookie:
                print(LEGACY_BUGZILLA_CREDENTIALS_DETECTED)

                # Clear legacy credentials automatically if an API Key is
                # found as it supercedes all other credentials.
                if bzapikey:
                    print('The legacy credentials have been removed.\n')
                    c.clear_legacy_bugzilla_credentials()
                elif self._prompt_yn('Remove legacy credentials'):
                    c.clear_legacy_bugzilla_credentials()

        # Look for and clean up old extensions.
        for ext in {'bzexport', 'qimportbz', 'mqext'}:
            path = os.path.join(self.ext_dir, ext)
            if os.path.exists(path):
                if self._prompt_yn('Would you like to remove the old and no '
                    'longer referenced repository at %s' % path):
                    print('Cleaning up old repository: %s' % path)
                    shutil.rmtree(path)

        # Python + Mercurial didn't have terrific TLS handling until Python
        # 2.7.9 and Mercurial 3.4. For this reason, it was recommended to pin
        # certificates in Mercurial config files. In modern versions of
        # Mercurial, the system CA store is used and old, legacy TLS protocols
        # are disabled. The default connection/security setting should
        # be sufficient and pinning certificates is no longer needed.
        have_modern_ssl = hasattr(ssl, 'SSLContext')
        if hg_version < LooseVersion('3.4') or not have_modern_ssl:
            c.add_mozilla_host_fingerprints()

        # We always update fingerprints if they are present. We /could/ offer to
        # remove fingerprints if running modern Python and Mercurial. But that
        # just adds more UI complexity and isn't worth it.
        c.update_mozilla_host_fingerprints()

        # References to multiple version-control-tools checkouts can confuse
        # version-control-tools, since various Mercurial extensions resolve
        # dependencies via __file__ and repos could reference another copy.
        seen_vct = set()
        for k, v in c.config.get('extensions', {}).items():
            if 'version-control-tools' not in v:
                continue

            i = v.index('version-control-tools')
            vct = v[0:i + len('version-control-tools')]
            seen_vct.add(os.path.realpath(os.path.expanduser(vct)))

        if len(seen_vct) > 1:
            print(MULTIPLE_VCT % c.config_path)

        # At this point the config should be finalized.

        if sys.platform != 'win32':
            # Config file may contain sensitive content, such as passwords.
            # Prompt to remove global permissions.
            mode = os.stat(config_path).st_mode
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                print(FILE_PERMISSIONS_WARNING)
                if self._prompt_yn('Remove permissions for others to '
                                   'read your hgrc file'):
                    # We don't care about sticky and set UID bits because
                    # this is a regular file.
                    mode = mode & stat.S_IRWXU
                    print('Changing permissions of %s' % config_path)
                    os.chmod(config_path, mode)

        print(FINISHED)
        return 0

    def can_use_extension(self, c, name, path=None):
        # Load extension to hg and search stdout for printed exceptions
        if not path:
            path = os.path.join(self.vcs_tools_dir, 'hgext', name)
        result = subprocess.check_output(['hg',
             '--config', 'extensions.testmodule=%s' % path,
             '--config', 'ui.traceback=true'],
            stderr=subprocess.STDOUT)
        return b"Traceback" not in result

    def prompt_external_extension(self, c, name, prompt_text, path=None):
        # Ask the user if the specified extension should be enabled. Defaults
        # to treating the extension as one in version-control-tools/hgext/
        # in a directory with the same name as the extension and thus also
        # flagging the version-control-tools repo as needing an update.
        if name not in c.extensions:
            if not self.can_use_extension(c, name, path):
                return
            print(name)
            print('=' * len(name))
            print('')
            if not self._prompt_yn(prompt_text):
                print('')
                return
        if not path:
            # We replace the user's home directory with ~ so the
            # config file doesn't depend on the path to the home
            # directory
            path = os.path.join(self.vcs_tools_dir.replace(os.path.expanduser('~'), '~'), 'hgext', name)
        c.activate_extension(name, path)
        print('Activated %s extension.\n' % name)

    def _prompt(self, msg, allow_empty=False):
        print(msg)

        while True:
            response = raw_input().decode('utf-8')

            if response:
                return response

            if allow_empty:
                return None

            print('You must type something!')

    def _prompt_yn(self, msg):
        print('%s? [Y/n]' % msg)

        while True:
            choice = raw_input().lower().strip()

            if not choice:
                return True

            if choice in ('y', 'yes'):
                return True

            if choice in ('n', 'no'):
                return False

            print('Must reply with one of {yes, no, y, n}.')