import os
import shutil
import string
import logging
from datetime import datetime

import tg
import git
from pylons import c

from ming.base import Object
from ming.orm import MappedClass, session
from ming.utils import LazyProperty

from allura import model as M
from allura.model.repository import topological_sort

log = logging.getLogger(__name__)

class Repository(M.Repository):
    tool_name='Git'
    repo_id='git'
    type_s='Git Repository'
    class __mongometa__:
        name='git-repository'

    def __init__(self, **kw):
        super(Repository, self).__init__(**kw)
        self._impl = GitImplementation(self)

    def readonly_clone_command(self):
        return 'git clone git://%s' % self.scm_url_path

    def readwrite_clone_command(self):
        return 'git clone ssh://%s@%s' % (c.user.username, self.scm_url_path)

class GitImplementation(M.RepositoryImplementation):
    post_receive_template = string.Template(
        '#!/bin/bash\n'
        'curl -s $url\n')

    def __init__(self, repo):
        self._repo = repo

    @LazyProperty
    def _git(self):
        try:
            return git.Repo(self._repo.full_fs_path)
        except (git.exc.NoSuchPathError, git.exc.InvalidGitRepositoryError), err:
            log.error('Problem looking up repo: %r', err)
            return None

    def init(self):
        fullname = self._setup_paths()
        log.info('git init %s', fullname)
        if os.path.exists(fullname):
            shutil.rmtree(fullname)
        repo = git.Repo.init(
            path=fullname,
            mkdir=True,
            quiet=True,
            bare=True,
            shared='all')
        self.__dict__['_git'] = repo
        self._setup_special_files()
        self._repo.status = 'ready'

    def clone_from(self, source_path):
        '''Initialize a repo as a clone of another'''
        fullname = self._setup_paths(create_repo_dir=False)
        if os.path.exists(fullname):
            shutil.rmtree(fullname)
        log.info('Initialize %r as a clone of %s',
                 self._repo, source_path)
        repo = git.Repo.clone_from(
            source_path,
            to_path=fullname,
            bare=True)
        self.__dict__['_git'] = repo
        self._setup_special_files()
        self._repo.status = 'analyzing'
        session(self._repo).flush()
        log.info('... %r cloned, analyzing', self._repo)
        self._repo.refresh()
        self._repo.status = 'ready'
        log.info('... %s ready', self._repo)
        session(self._repo).flush()

    def commit(self, rev):
        result = M.Commit.query.get(repo_id='git', object_id=rev)
        if result is None:
            impl = self._git.rev_parse(str(rev) + '^0')
            result = M.Commit.query.get(repo_id='git', object_id=impl.hexsha)
        if result is None: return None
        result.set_context(self._repo)
        return result

    def new_commits(self):
        graph = {}
        to_visit = [ self._git.commit(rev=h.object_id) for h in self._repo.heads ]
        while to_visit:
            obj = to_visit.pop()
            if obj.hexsha in graph: continue
            graph[obj.hexsha] = set(p.hexsha for p in obj.parents)
            to_visit += obj.parents
        return [
            oid for oid in topological_sort(graph)
            if M.Commit.query.find(dict(repo_id='git', object_id=oid)).count() == 0 ]

    def commit_context(self, commit):
        prev_ids = commit.parent_ids
        prev = M.Commit.query.find(dict(
                repo_id='git',
                object_id={'$in':prev_ids})).all()
        next = M.Commit.query.find(dict(
                repo_id='git',
                parent_ids=commit.object_id,
                repositories=self._repo._id)).all()
        for ci in prev + next:
            ci.set_context(self._repo)
        return dict(prev=prev, next=next)

    def refresh_heads(self):
        self._repo.heads = [
            Object(name=head.name, object_id=head.commit.hexsha)
            for head in self._git.heads
            if head.is_valid() ]
        self._repo.branches = [
            Object(name=head.name, object_id=head.commit.hexsha)
            for head in self._git.branches
            if head.is_valid() ]
        self._repo.repo_tags = [
            Object(name=tag.name, object_id=tag.commit.hexsha)
            for tag in self._git.tags
            if tag.is_valid() ]
        session(self._repo).flush()

    def refresh_commit(self, ci):
        obj = self._git.commit(ci.object_id)
        ci.tree_id = obj.tree.hexsha
        # Save commit metadata
        ci.committed = Object(
            name=obj.committer.name,
            email=obj.committer.email,
            date=datetime.fromtimestamp(
                obj.committed_date-obj.committer_tz_offset))
        ci.authored=Object(
            name=obj.author.name,
            email=obj.author.email,
            date=datetime.fromtimestamp(
                obj.authored_date-obj.author_tz_offset))
        ci.message=obj.message or ''
        ci.parent_ids=[ p.hexsha for p in obj.parents ]
        # Save commit tree
        tree, isnew = M.Tree.upsert('git', obj.tree.hexsha)
        if isnew:
            tree.set_context(ci)
            tree.set_last_commit(ci)
            self._refresh_tree(tree, obj.tree)

    def log(self, object_id, skip, count):
        obj = self._git.commit(object_id)
        candidates = [ obj ]
        result = []
        seen = set()
        while count and candidates:
            obj = candidates.pop(0)
            if obj.hexsha in seen: continue
            seen.add(obj.hexsha)
            if skip == 0:
                result.append(obj.hexsha)
                count -= 1
            else:
                skip -= 1
            candidates += obj.parents
        return result, [ p.hexsha for p in candidates ]

    def open_blob(self, blob):
        return _OpenedGitBlob(
            self._object(blob.object_id).data_stream)

    def _setup_receive_hook(self):
        'Set up the git post-commit hook'
        text = self.post_receive_template.substitute(
            url=tg.config.get('base_url', 'localhost:8080')
            + self._repo.url()[1:] + 'refresh')
        fn = os.path.join(self._repo.fs_path, self._repo.name, 'hooks', 'post-receive')
        with open(fn, 'w') as fp:
            fp.write(text)
        os.chmod(fn, 0755)

    def _refresh_tree(self, tree, obj):
        tree.object_ids = Object(
            (o.hexsha, o.name) for o in obj )
        for o in obj.trees:
            subtree, isnew = M.Tree.upsert('git', o.hexsha)
            if isnew:
                subtree.set_context(tree, o.name)
                subtree.set_last_commit(tree.commit)
                self._refresh_tree(subtree, o)
        for o in obj.blobs:
            blob, isnew = M.Blob.upsert('git', o.hexsha)
            if isnew:
                blob.set_context(tree, o.name)
                blob.set_last_commit(tree.commit)

    def _object(self, oid):
        evens = oid[::2]
        odds = oid[1::2]
        binsha = ''
        for e,o in zip(evens, odds):
            binsha += chr(int(e+o, 16))
        return git.Object.new_from_sha(self._git, binsha)

class _OpenedGitBlob(object):
    CHUNK_SIZE=4096

    def __init__(self, stream):
        self._stream = stream

    def read(self):
        return self._stream.read()

    def __iter__(self):
        buffer = ''
        while True:
            # Replenish buffer
            while '\n' not in buffer:
                chars = self._stream.read(self.CHUNK_SIZE)
                if not chars: break
                buffer += chars
            if not buffer: break
            eol = buffer.find('\n')
            yield buffer[:eol+1]
            buffer = buffer[eol+1:]

    def close(self):
        pass

MappedClass.compile_all()
