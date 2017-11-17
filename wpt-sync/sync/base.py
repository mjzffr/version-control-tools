import json
import os
import re
import shutil
import subprocess
import traceback

import git

import log
import commit as sync_commit
from env import Environment

env = Environment()

invalid_re = re.compile(".*[-_]")

logger = log.get_logger("base")


class ProcessName(object):
    """Representation of a refname, excluding the refs/<type>/ prefix
    that is used to identify a sync operation.  This has the general form
    <obj type>/<status>/<obj_id>[/<seq_id>]

    Here <obj type> represents the type of process e.g upstream or downstream,
    <status> is the current sync status, <obj_id> is an identifier for the sync,
    typically either a bug number or PR number, and <seq_id> is an optional id
    to cover cases where we might have multiple processes with the same obj_id.

    This format means we are able to query all syncs with a given status easilly
    by doing e.g.

    git for-each-ref upstream/open/*

    However it also means that we need to be able to update the ref name
    whereever it is used when the status changes. This can be both in mutliple
    repositories and with multiple types (e.g. tags vs branches). Therefore we
    "attach" every VcsRefObject representing a concrete use of a ref to this
    class and when changing the status propogate out the change to all users.
    """
    def __init__(self, obj_type, status, obj_id, seq_id=None):
        self._obj_type = obj_type
        self._status = status
        self._obj_id = str(obj_id)
        self._seq_id = str(seq_id) if seq_id is not None else None
        self._refs = []

    def __str__(self):
        return self.name(self._obj_type,
                         self._status,
                         self._obj_id,
                         self._seq_id)

    @staticmethod
    def name(obj_type, status, obj_id, seq_id=None):
        rv = "sync/%s/%s/%s" % (obj_type,
                                status,
                                obj_id)
        if seq_id is not None:
            rv = "%s/%s" % (rv, seq_id)
        return rv

    def attach_ref(self, ref):
        # This two-way binding is kind of nasty, but we want the
        # refs to update every time we update the property here
        # and that makes it easy to achieve
        self._refs.append(ref)
        return self

    def delete(self):
        for ref in self._refs:
            ref.delete()

    @property
    def obj_type(self):
        return self._obj_type

    @property
    def obj_id(self):
        return self._obj_id

    @property
    def seq_id(self):
        return self._seq_id

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, value):
        if self._status != value:
            self._status = value
            for ref_obj in self._refs:
                ref_obj.rename()

    @classmethod
    def from_ref(cls, ref):
        parts = ref.split("/")
        if parts[0] == "refs":
            parts = parts[2:]
        if parts[0] == "sync":
            parts = parts[1:]
        return cls(*parts)

    @classmethod
    def commit_refs(cls, repo, ref_type, obj_type, status="open", obj_id="*", seq_id=None):
        branch_filter = "refs/%s/sync/%s/%s/%s" % (ref_type, obj_type, status, obj_id)
        if seq_id is not None:
            branch_filter = "%s/%s" % (branch_filter, seq_id)
        commits_refs = repo.git.for_each_ref("--format", "%(objectname) %(refname:short)",
                                             branch_filter)

        commit_refs = {}
        for line in commits_refs.split("\n"):
            line = line.strip()
            if not line:
                continue
            commit, ref = line.split(" ", 1)
            ref = "/".join(ref.split("/")[1:])
            commit_refs[commit] = ref
        return commit_refs


class SyncPointName(object):
    """Like a process name but for pointers that aren't associated with a
    specific sync object, but with a general process e.g. the last update point
    for an upstream sync."""
    def __init__(self, obj_type, obj_id):
        self._obj_type = obj_type
        self._obj_id = obj_id
        self._refs = []

    def attach_ref(self, ref):
        return self

    def __str__(self):
        return self.name(self._obj_type,
                         self._obj_id)

    @staticmethod
    def name(obj_type, obj_id):
        return "sync/%s/%s" % (obj_type,
                               obj_id)


class VcsRefObject(object):
    """Representation of a named reference to a git object associated with a
    specific process_name.

    This is typically either a tag or a head (i.e. branch), but can be any
    git object."""

    ref_prefix = None

    def __init__(self, repo, process_name, commit_cls=sync_commit.Commit):
        self.repo = repo
        self._process_name = process_name.attach_ref(self)
        self.commit_cls = commit_cls
        self._ref = str(process_name)

    def __str__(self):
        return self._ref

    def delete(self):
        self.repo.git.update_ref("-d", self.path)

    @classmethod
    def process_path(cls, process_name):
        return "refs/%s/%s" % (cls.ref_prefix, str(process_name))

    @property
    def path(self):
        return "refs/%s/%s" % (self.ref_prefix, self._ref)

    @property
    def ref(self):
        ref = git.Reference(self.repo, self.path)
        if ref.is_valid():
            return ref
        return None

    @classmethod
    def create(cls, repo, process_name, obj):
        path = cls.process_path(process_name)
        ref = git.Reference(repo, path)
        ref.set_object(obj)
        return cls(repo, process_name)

    @property
    def commit(self):
        ref = self.ref
        if ref is not None:
            commit = self.commit_cls(self.repo, ref.commit)
            return commit

    @commit.setter
    def commit(self, commit):
        if isinstance(commit, sync_commit.Commit):
            commit = commit.commit
        ref = git.Reference(self.repo, self.path)
        ref.set_object(commit)

    def rename(self):
        ref = self.ref
        if not ref:
            return
        # Pretty sure there is an easier way to do this
        self._ref = str(self._process_name)
        new_ref = git.Reference(self.repo, self.path)
        new_ref.set_object(ref.commit.hexsha)
        ref.delete(self.repo, ref.path)

    @classmethod
    def load_all(cls, repo, obj_type, status="open", obj_id="*", seq_id=None,
                 commit_cls=sync_commit.Commit):
        rv = []
        for ref_name in sorted(ProcessName.commit_refs(repo,
                                                       ref_type=cls.ref_prefix,
                                                       obj_type=obj_type,
                                                       status=status,
                                                       obj_id=obj_id,
                                                       seq_id=seq_id).itervalues()):
            rv.appe.end(cls(repo, ProcessName.from_ref(ref_name), commit_cls))
        return rv


class BranchRefObject(VcsRefObject):
    ref_prefix = "heads"

    def rename(self):
        ref = self.ref
        if not ref:
            return
        self._ref = str(self._process_name)
        self.repo.git.branch(ref.name, self._ref, move=True)


class DataRefObject(VcsRefObject):
    ref_prefix = "syncs"


def create_commit(repo, tree, message, parents=None, commit_cls=sync_commit.Commit):
    """
    :param tree: A dictionary of path: file data
    """
    # TODO: use some lock around this since it writes to the index

    # First we create an empty index
    repo.git.read_tree(empty=True)
    for path, data in tree.iteritems():
        proc = repo.git.hash_object(w=True, path=path, stdin=True, as_process=True,
                                    istream=subprocess.PIPE)
        stdout, stderr = proc.communicate(data)
        if proc.returncode is not 0:
            raise git.GitCommandError(["git", "hash-object", "-w", "--path=%s" % path],
                                      proc.returncode, stderr, stdout)
        repo.git.update_index("100644", stdout, path, add=True, cacheinfo=True)
    tree_id = repo.git.write_tree()
    args = ["-m", message]
    if parents is not None:
        for item in parents:
            args.extend(["-p", item])
    args.append(tree_id)
    sha1 = repo.git.commit_tree(*args)
    return commit_cls(repo, sha1)


class ProcessData(object):
    """Wrapper object for providing a dictionary-like API over data
    stored in a git tag object, which automatically updates the
    object whenever the data is changed"""
    path = "data"

    def __init__(self, repo, process_name):
        self._ref = DataRefObject(repo, process_name)
        self._data = self._load()

    @property
    def repo(self):
        return self._ref.repo

    @classmethod
    def load(cls, repo, branch_name):
        process_name = ProcessName.from_ref(branch_name)
        if not process_name:
            return None
        return cls(repo, process_name)

    @classmethod
    def create(cls, repo, process_name, data, message="Sync data"):
        commit = cls._create_commit(repo, process_name, data, message=message)
        DataRefObject.create(repo, process_name, commit.commit)
        return cls(repo, process_name)

    @classmethod
    def _create_commit(cls, repo, process_name, data, message="Sync data", parents=None):
        tree = {cls.path: json.dumps(data)}
        return create_commit(repo, tree, message=message, parents=parents)

    def _save(self, message="Sync data"):
        commit = self._create_commit(self.repo, self._ref._process_name, self._data,
                                     parents=[self._ref.path], message=message)
        self._ref.ref.set_commit(commit.sha1)

    def _load(self):
        return json.load(self._ref.ref.commit.tree[self.path].data_stream)

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        if key not in self._data or self._data[key] != value:
            self._data[key] = value
            self._save(message="Update %s" % key)

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)


class CommitFilter(object):
    """Filter of a range of commits"""
    def path_filter(self):
        """Path filter for the commit range,
        returning a list of paths that match or None to
        match all paths."""
        return None

    def filter_commit(self, commit):
        """Per-commit filter.

        :param commit: wpt_commit.Commit object
        :returns: A boolean indicating whether to include the commit"""
        return True

    def filter_commits(self, commits):
        """Filter that applies to the set of commits that were selected
        by the per-commit filter. Useful for e.g. removing backouts
        from a set of commits.

        :param commits: List of wpt_commit.Commit objects
        :returns: List of commits to return"""
        return commits


class CommitRange(object):
    """Range of commits in a specific repository.

    This is assumed to be between a tag and a branch sharing a single process_name
    i.e. the tag represents the base commit of the branch.
    TODO:  Maybe just store the base branch name in the tag rather than making it
           an actual pointer since that works better with rebases.
    """
    def __init__(self, repo, base, head_ref, commit_cls, commit_filter=None):
        self.repo = repo

        # This ended up a little confused because these used to both be
        # VcsRefObjects, but now the base is stored as a ref not associated
        # with a process_name. This should be refactored.
        self._base = commit_cls(repo, base)
        self._head_ref = head_ref
        self.commit_cls = commit_cls
        self.commit_filter = commit_filter

        # Cache for the commits in this range
        self._commits = None
        self._head_sha = None
        self._base_sha = None

    def __getitem__(self, index):
        return self.commits[index]

    def __iter__(self):
        for item in self.commits:
            yield item

    def __len__(self):
        return len(self.commits)

    @property
    def base(self):
        return self._base

    # Base doesn't have a setter here because we don't have a good way to update
    # the ref where it's stored

    @property
    def head(self):
        return self._head_ref.commit

    @head.setter
    def head(self, value):
        self._head_ref.commit = value

    @property
    def commits(self):
        if self._commits:
            if (self.head.sha1 == self._head_sha and
                self.base.sha1 == self._base_sha):
                return self._commits
        revish = "%s..%s" % (self.base.sha1, self.head.sha1)
        commits = []
        for git_commit in self.repo.iter_commits(revish,
                                                 reverse=True,
                                                 paths=self.commit_filter.path_filter()):
            commit = self.commit_cls(self.repo, git_commit)
            if not self.commit_filter.filter_commit(commit):
                continue
            commits.append(commit)
        commits = self.commit_filter.filter_commits(commits)
        self._commits = commits
        self._head_sha = self.head.sha1
        self._base_sha = self.base.sha1
        return self._commits


class Worktree(object):
    """Wrapper for accessing a git worktree for a specific process.

    To access the worktree call .get()
    """

    def __init__(self, repo, process_name):
        self.repo = repo
        self._worktree = None
        self.process_name = process_name
        self.path = os.path.join(env.config["root"],
                                 env.config["paths"]["worktrees"],
                                 os.path.basename(repo.working_dir),
                                 process_name.obj_type,
                                 process_name.obj_id)

    def get(self):
        """Return the worktree.

        On first access, the worktree is reset to the current HEAD. Subsequent
        access doesn't perform the same check, so it's possible to retain state
        within a specific process."""
        if self._worktree is None:
            if os.path.exists(self.path):
                worktree = git.Repo(self.path)
                worktree.index.reset(str(self.process_name), working_tree=True)
            else:
                self.repo.git.worktree("prune")
                worktree = self.repo.git.worktree("add",
                                                  os.path.abspath(self.path),
                                                  str(self.process_name))
            self._worktree = git.Repo(self.path)
        if self._worktree.active_branch.name != str(self.process_name):
            # I think this can only happen when we rename the reference
            self._worktree.git.checkout(str(self.process_name))
        return self._worktree

    def delete(self):
        if os.path.exists(self.path):
            try:
                shutil.rmtree(self.path)
            except Exception:
                logger.warning("Failed to remove worktree %s:%s" %
                               (self.path, traceback.format_exc()))
            else:
                logger.debug("Removed worktree %s" % (self.path,))


class SyncProcess(object):
    sync_type = "*"
    obj_id = None  # Either "bug" or "pr"

    def __init__(self, git_gecko, git_wpt, process_name):
        self.git_gecko = git_gecko
        self.git_wpt = git_wpt

        self._process_name = process_name

        self.data = ProcessData(git_gecko, self._process_name)

        self.gecko_commits = CommitRange(git_gecko,
                                         self.data["gecko-base"],
                                         BranchRefObject(git_gecko,
                                                         self._process_name,
                                                         commit_cls=sync_commit.GeckoCommit),
                                         commit_cls=sync_commit.GeckoCommit,
                                         commit_filter=self.gecko_commit_filter())
        self.wpt_commits = CommitRange(git_wpt,
                                       self.data["wpt-base"],
                                       BranchRefObject(git_wpt,
                                                       self._process_name,
                                                       commit_cls=sync_commit.WptCommit),
                                       commit_cls=sync_commit.WptCommit,
                                       commit_filter=self.wpt_commit_filter())

        self.gecko_worktree = Worktree(git_gecko,
                                       self._process_name)
        self.wpt_worktree = Worktree(git_wpt,
                                     self._process_name)

    def __eq__(self, other):
        if not hasattr(other, "_process_name"):
            return False
        str(self._process_name) == str(other._process_name)

    def __ne__(self, other):
        return not self == other

    def set_wpt_base(self, ref):
        # This is kind of an appaling hack
        if ref not in self.git_wpt.refs:
            raise ValueError
        self.data["wpt-base"] = ref
        self.wpt_commits._base = sync_commit.WptCommit(self.git_wpt, ref)

    def delete(self):
        for worktree in [self.gecko_worktree, self.wpt_worktree]:
            worktree.delete()
        self._process_name.delete()

    @staticmethod
    def gecko_integration_branch():
        return env.config["gecko"]["refs"]["mozilla-inbound"]

    def gecko_commit_filter(self):
        return CommitFilter()

    def wpt_commit_filter(self):
        return CommitFilter()

    def update_status(self, action, merged):
        """Update the status of the sync for a PR change"""
        pass

    @classmethod
    def new(cls, git_gecko, git_wpt, gecko_base, gecko_head,
            wpt_base="origin/master", wpt_head=None,
            bug=None, pr=None, status="open"):
        if cls.obj_id == "bug":
            assert bug is not None
            obj_id = bug
        elif cls.obj_id == "pr":
            assert pr is not None
            obj_id = pr
        else:
            raise ValueError("Invalid cls.obj_id: %s" % cls.obj_id)

        if wpt_head is None:
            wpt_head = wpt_base

        data = {"gecko-base": gecko_base,
                "wpt-base": wpt_base,
                "pr": pr,
                "bug": bug}

        # This is pretty ugly
        process_name = ProcessName(cls.sync_type, status, obj_id)
        ProcessData.create(git_gecko, process_name, data)
        BranchRefObject.create(git_gecko, process_name, gecko_head)
        BranchRefObject.create(git_wpt, process_name, wpt_head)

        rv = cls(git_gecko, git_wpt, process_name)
        return rv

    @classmethod
    def load(cls, git_gecko, git_wpt, branch_name):
        process_name = ProcessName.from_ref(branch_name)
        if not process_name:
            return None
        return cls(git_gecko, git_wpt, process_name)

    @classmethod
    def load_all(cls, git_gecko, git_wpt, status="open", obj_id="*"):
        rv = []
        for branch_name in ProcessName.commit_refs(git_gecko,
                                                   ref_type="heads",
                                                   obj_type=cls.sync_type,
                                                   status=status,
                                                   obj_id=obj_id).itervalues():
            rv.append(cls.load(git_gecko, git_wpt, branch_name))
        return rv

    @property
    def branch_name(self):
        return str(self._process_name)

    @property
    def status(self):
        return self._process_name.status

    @status.setter
    def status(self, value):
        self._process_name.status = value

    @property
    def bug(self):
        if self.obj_id == "bug":
            return self._process_name.obj_id
        else:
            return self.data.get("bug")

    @bug.setter
    def bug(self, value):
        if self.obj_id == "bug":
            raise AttributeError("Can't set attribute")
        self.data["bug"] = value

    @property
    def pr(self):
        if self.obj_id == "pr":
            return self._process_name.obj_id
        else:
            return self.data.get("pr")

    @pr.setter
    def pr(self, value):
        if self.obj_id == "pr":
            raise AttributeError("Can't set attribute")
        self.data["pr"] = value

    def gecko_rebase(self, new_base):
        new_base = sync_commit.GeckoCommit(self.git_gecko, new_base)
        git_worktree = self.gecko_worktree.get()
        git_worktree.git.rebase(new_base.sha1)
        self.git_commits.base = new_base

    def wpt_rebase(self, ref):
        assert ref in self.git_wpt.refs
        git_worktree = self.wpt_worktree.get()
        git_worktree.git.rebase(ref)
        self.set_wpt_base(ref)
