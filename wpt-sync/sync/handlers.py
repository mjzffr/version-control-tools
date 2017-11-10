import traceback
import urlparse

import downstream
import log
import push
import upstream
import worktree
from model import PullRequest, Sync, SyncDirection
from gitutils import is_ancestor, pr_for_commit

logger = log.get_logger("handlers")


def log_exceptions(f):
    def inner(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.critical("%s failed with error:%s" % (f.__name__, traceback.format_exc(e)))
            # For now:
            raise

    inner.__name__ = f.__name__
    inner.__doc__ = f.__doc__
    return inner


def get_sync(session, pr_id):
    return session.query(Sync).filter(Sync.pr_id == pr_id).first()


class Handler(object):
    def __init__(self, config):
        self.config = config

    def __call__(self, session, git_gecko, git_wpt, gh_wpt, bz, body):
        raise NotImplementedError


def handle_pr(config, session, git_gecko, git_wpt, gh_wpt, bz, event):
    pr_id = event['number']
    sync = get_sync(session, pr_id)

    gh_wpt.load_pull(event["pull_request"])
    PullRequest.update_from_github(session, event["pull_request"])

    if not sync:
        # If we don't know about this sync then it's a new thing that we should
        # set up state for
        # TODO: maybe want to create a new sync here irrespective of the event
        # type because we missed some events.
        if event["action"] == "opened":
            downstream.new_wpt_pr(config, session, git_gecko, git_wpt, bz, event["payload"])
    elif sync.direction == SyncDirection.upstream:
        # This is a PR we created, so ignore it for now
        pass
    elif sync.direction == SyncDirection.downstream:
        if event["action"] == "closed":
            # TODO - close the related bug, cancel try runs, etc.
            pass
        # TODO It's a PR we already started to downstream, so update as appropriate


def handle_status(config, session, git_gecko, git_wpt, gh_wpt, bz, event):
    if event["context"] == "upstream/gecko":
        # Never handle changes to our own status
        return

    rev = event["sha"]
    pr_id = pr_for_commit(git_wpt, rev)

    if not pr_id:
        if not is_ancestor(git_wpt, rev, "origin/master"):
            logger.debug(event)
            logger.error("Got status for commit %s, but that isn't the head of any PR" % rev)
        return
    else:
        logger.info("Got status for commit %s from PR %s" % (rev, pr_id))

    sync = get_sync(session, pr_id)

    if not sync:
        # Presumably this is a thing we ought to be downstreaming, but missed somehow
        logger.info("Got a status update for PR %s which is unknown to us" % pr_id)
        pr_data = gh_wpt.get_pull(pr_id)
        sync = downstream.new_wpt_pr(config, session, git_gecko, git_wpt, bz, pr_data.raw_data)

    if sync.direction == SyncDirection.upstream:
        upstream.status_changed(git_gecko, git_wpt, sync, event["context"], event["status"],
                                event["url"], event["sha"])
    elif sync.direction == SyncDirection.downstream:
        downstream.status_changed(git_gecko, git_wpt, sync, event["context"], event["status"],
                                  event["url"], event["sha"])


def handle_pr_merge():
    # prepare to land downstream
    pass


def handle_pr_approved():
    # prepare to land downstream
    pass


def handle_push(config, session, git_gecko, git_wpt, gh_wpt, bz, event):
    push.wpt_push(session, git_wpt, gh_wpt, [item["id"] for item in event["commits"]])


class GitHubHandler(Handler):
    dispatch_event = {
        "pull_request": handle_pr,
        "status": handle_status,
        "push": handle_push,
    }

    def __call__(self, session, git_gecko, git_wpt, gh_wpt, bz, body):
        handler = self.dispatch_event[body["event"]]
        if handler:
            return handler(self.config, session, git_gecko, git_wpt, gh_wpt, bz, body["payload"])
        # TODO: other events to check if we can merge a PR
        # because of some update


class PushHandler(Handler):
    def __init__(self, config):
        self.config = config
        self.integration_repos = {}
        for repo_name, url in config["sync"]["integration"].iteritems():
            url_parts = urlparse.urlparse(url)
            url = urlparse.urlunparse(("https",) + url_parts[1:])
            self.integration_repos[url] = repo_name
        self.landing_repo = config["sync"]["landing"]

    def __call__(self, session, git_gecko, git_wpt, gh_wpt, bz, body):
        data = body["payload"]["data"]
        repo_url = data["repo_url"]
        # Not sure if it's everey possible to get multiple heads here in a way that
        # matters for us
        rev = data["heads"][0]
        logger.debug("Commit landed in repo %s" % repo_url)
        if repo_url in self.integration_repos or repo_url == self.landing_repo:
            if repo_url in self.integration_repos:
                repo_name = self.integration_repos[repo_url]
                upstream.integration_commit(self.config, session, git_gecko, git_wpt, gh_wpt,
                                            bz, rev, repo_name)
            elif repo_url == self.landing_repo:
                upstream.landing_commit(self.config, session, git_gecko, git_wpt, gh_wpt, bz, rev)


class TaskHandler(Handler):
    def __call__(self, session, git_gecko, git_wpt, gh_wpt, bz, body):
        if not (body.get("origin")
                and body["origin"].get("revision")
                and body.get("taskId")):
            logger.debug("Oh no, this payload doesn't have the format we expect!"
                         "Need 'revision' and 'taskId'. Got:\n{}\n".format(body))
            return

        return downstream.update_taskgroup(body["origin"]["revision"],
                                           body["taskId"],
                                           body["result"])


class TaskGroupHandler(Handler):
    def __call__(self, session, git_gecko, git_wpt, gh_wpt, bz, body):
        return downstream.on_taskgroup_resolved(
            self.config,
            session,
            git_gecko,
            bz,
            body["taskGroupId"])


class LandingHandler(Handler):
    def __call__(self, session, git_gecko, git_wpt, gh_wpt, bz):
        return push.land_to_gecko(self.config, session, git_wpt, git_wpt, gh_wpt, bz)


class CleanupHandler(Handler):
    def __call__(self, session, git_gecko, git_wpt, gh_wpt, bz):
        return worktree.cleanup(self.config, session)
