import os
import shutil
import traceback
import datetime

import git

import log
from model import Landing, Sync

logger = log.get_logger("worktree")


def get_worktree_path(config, session, repo, project, prefix):
    """
    Args:
        config (dict)
        session (orm Session)
        repo (git.Repo)
        project (str): web-platform-tests or gecko
        prefix (str): base name for worktree/branch

    Returns:
        str: relative path to worktree
    """
    base_path = os.path.join(project, prefix)
    count = 0
    while True:
        rel_path = base_path
        if count > 0:
            rel_path = "%s-%i" % (rel_path, count)
        path = os.path.join(config["root"], config["paths"]["worktrees"], rel_path)
        branch_name = os.path.split(rel_path)[1]
        if not (os.path.exists(path) or
                branch_name in repo.branches or
                query_worktree(session, project, rel_path)):
            return path
        count += 1


def ensure_worktree(config, session, repo, project, sync, prefix, base):
    """
    Args:
        config (dict)
        session (orm Session)
        repo (git.Repo)
        project (str): web-platform-tests or gecko
        sync (model.Sync)
        prefix (str)
        base (str): e.g. origin/master

    Returns:
        (git.Repo, str): worktree repo, branch name
    """
    if sync is not None:
        repo_worktree = worktree_attr(project)
        if not getattr(sync, repo_worktree):
            path = get_worktree_path(config, session, repo, project, prefix)
            setattr(sync, repo_worktree, path)
            logger.info("Setting up worktree in path %s" % path)

        worktree_path = os.path.join(config["root"],
                                     config["paths"]["worktrees"],
                                     getattr(sync, repo_worktree))
    else:
        worktree_path = get_worktree_path(config, session, repo, project, prefix)

    # TODO: If we want to prune these need to be careful about atomicity here
    # Probably need to have a lock whilst the cleanup is running
    if not os.path.exists(worktree_path):
        base_dir, branch_name = os.path.split(worktree_path)
        try:
            os.makedirs(base_dir)
        except OSError:
            pass
        repo.git.worktree("add", "-b", branch_name,
                          os.path.abspath(worktree_path),
                          base)
    else:
        branch_name = os.path.split(worktree_path)[1]
    git_work = git.Repo(worktree_path)

    return git_work, branch_name


def remove_worktrees(config, sync):
    # TODO: periodically delete old branches also?
    for rel_path in [sync.gecko_worktree, sync.wpt_worktree]:
        if not rel_path:
            continue
        worktree_path = os.path.join(config["root"],
                                     config["paths"]["worktrees"],
                                     rel_path)
        if os.path.exists(worktree_path):
            try:
                shutil.rmtree(worktree_path)
            except Exception:
                logger.warning("Failed to remove worktree %s:%s" %
                               (worktree_path, traceback.format_exc()))
            else:
                logger.debug("Removed worktree %s" % (worktree_path,))


def worktree_attr(project):
    assert project in ["web-platform-tests", "gecko"]
    if project == "web-platform-tests":
        return "wpt_worktree"
    else:
        return "gecko_worktree"


def query_worktree(session, project, value):
    column = worktree_attr(project)
    return session.query(Sync).filter(getattr(Sync, column) == value).first()


def cleanup(config, session):
    work_base = os.path.join(config["root"], config["sync"]["worktrees"])

    current_trees = {}

    for project in ["gecko", "web-platform-tests"]:
        project_path = os.path.join(work_base, project)
        for worktree_name in os.listdir(project_path):
            if not os.path.isdir(path):
                continue

            worktree_path = os.path.join(project_path, worktree_name)

            rel_path = os.path.relpath(worktree_path, work_base)
            sync = query_worktree(session, project, rel_path)
            landing = session.query(Landing).filter(Landing.worktree == rel_path)

            if landing is not None:
                continue

            if sync is None:
                # This is an orpaned worktree
                logger.info("Removing orphan worktree %s" % worktree_path)
                shutil.rmtree(worktree_path)
            elif (sync.direction == SyncDirection.downstream and sync.imported or
                  sync.direction == SyncDirection.upstream and sync.merged):
                # Sync is finished so clean up
                logger.info("Removing worktree for completed sync %s" % worktree_path)
                shutil.rmtree(worktree_path)
            else:
                current_trees[worktree_path] = sync

    now = datetime.datetime.now()
    for worktree_path, sync in current_trees.iteritems():
        path = os.path.join(work_base, worktree_path)
        if sync.modified < now - datetime.timedelta(days=7):
            # Sync hasn't been touched in a week
            if (datetime.datetime.fromtimestamp(os.stat(worktree_path).st_mtime) <
                now - datetime.timedelta(days=2)):
                # And worktree hasn't been touched for two days
                logger.info("Removing worktree without recent activity %s" % worktree_path)
                shutil.rmtree(worktree_path)

    # TODO: Add a hard cutoff of the number of allowed worktrees