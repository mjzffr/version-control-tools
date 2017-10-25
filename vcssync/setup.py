import os
from setuptools import setup, find_packages

console_scripts = [
    'linearize-git=mozvcssync.cli:linearize_git',
    'linearize-git-to-hg=mozvcssync.cli:linearize_git_to_hg',
    'overlay-hg-repos=mozvcssync.cli:overlay_hg_repos_cli',
    'servo-backout-pr-cli=mozvcssync.cli:servo_backout_pr_cli',
    'servo-overlay=mozvcssync.servo:overlay_cli',
    'servo-pulse-listen=mozvcssync.servo:pulse_daemon',
    'test-apply-changes=mozvcssync.util:test_apply_changes_from_list',
    'wpt-pulse-listen=mozvcssync.wpt:pulse_daemon',
]

# These commands are really only useful for testing. So don't expose them by
# default.
if 'VCSSYNC_ENABLE_TESTING_COMMANDS' in os.environ:
    console_scripts.extend([
    ])

# ansible/roles/vcs-sync/defaults/main.yml must be updated if this package's
# version number is changed.

setup(
    name='mozvcssync',
    version='0.1',
    description='Synchronize changes across VCS repositories',
    url='https://mozilla-version-control-tools.readthedocs.io/',
    author='Mozilla',
    author_email='dev-version-control@lists.mozilla.org',
    license='MPL 2.0',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Programming Language :: Python :: 2.7',
    ],
    packages=find_packages(),
    entry_points={
        'console_scripts': console_scripts,
    },
    install_requires=[
        'dulwich>=0.16',
        'github3.py>=0.9.6',
        'kombu>=3.0.37',
        'Mercurial>=4.1',
    ],
)
