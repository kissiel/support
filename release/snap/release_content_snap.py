#!/usr/bin/env python3
# This file is part of Checkbox.
#
# Copyright 2019 Canonical Ltd.
# Written by:
#   Sylvain Pineau <sylvain.pineau@canonical.com>
#
# Checkbox is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3,
# as published by the Free Software Foundation.
#
# Checkbox is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Checkbox.  If not, see <http://www.gnu.org/licenses/>.
#
# TODO:
# Collect the revision/tracks to create a list of release commands for stable
# See https://git.launchpad.net/~snappy-dev/core-snap/tree/cron-scripts/lp-build-core
# See https://api.launchpad.net/devel.html#snap_build
# Create a Jenkins job to tag and bump version and merge back,
# tag is required to create diff/changelog
# Generate changelog as build artifact


import argparse
import datetime
import logging
import os
import re
import shutil
import subprocess
from urllib.parse import urlparse, urlunparse

from ruamel.yaml import YAML


parts_do_not_tag = ['acpi-tools', 'fwts', 'stress-ng']
parts_ignore = [
    'plainbox-provider-engineering-tests', 'tpm2-tss', 'tpm2-tools-3',
    'librealsense']


class ConsoleFormatter(logging.Formatter):

    """Custom Logging Formatter to ease copy paste of commands."""

    def format(self, record):
        fmt = '%(message)s'
        if record.levelno == logging.ERROR:
            fmt = "%(levelname)-8s %(message)s"
        result = logging.Formatter(fmt).format(record)
        return result


# create logger
logger = logging.getLogger('release')
logger.setLevel(logging.DEBUG)
# create file handler which logs even debug messages
fh = logging.FileHandler('release.log', mode='w')
fh.setLevel(logging.DEBUG)
# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
# create formatter and add it to the handlers
fh_formatter = logging.Formatter('%(asctime)-15s %(levelname)-8s %(message)s')
fh.setFormatter(fh_formatter)
ch.setFormatter(ConsoleFormatter())
# add the handlers to the logger
logger.addHandler(fh)
logger.addHandler(ch)


def run(*args, **kwargs):
    """wrapper for subprocess.run."""
    try:
        logger.debug(' '.join(*args))
        return subprocess.run(
            *args, **kwargs,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        logger.error('{}\n{}'.format(e, e.output.decode()))
        raise SystemExit(1)


class Release():

    """The Release command."""

    CWD = 'src'

    def __init__(self, args):
        self.branch = args.branch
        self.release_branch = args.release_branch
        self.finish_release = args.finish
        self.rebase_branch = args.rebase_branch
        self.increment_part = args.increment_part
        self.dry_run = args.dry_run
        self.user = args.user

        self.base_url = 'git+ssh://{}@git.launchpad.net'.format(self.user)
        self.full_url = os.path.join(self.base_url, args.repository)
        self.snap = os.path.basename(self.full_url)

        self._parts = {}
        self._previous_parts = {}

        self.clone_dir = os.path.join(self.CWD, self.snap)
        self._snapcraft_file = os.path.join(
            self.clone_dir, 'snap', 'snapcraft.yaml')

        self._yaml = YAML()
        self._yaml.preserve_quotes = True
        self._yaml.indent(mapping=2, sequence=2, offset=2)
        self.new_tag = '{:snap-%Y-%m-%dT%H%M}'.format(
            datetime.datetime.utcnow())
        self._cleanup_release_tags_commands = []

    def dance(self):
        self._cleanup()
        if self.rebase_branch:
            self._clone(self.full_url, self.branch)
            self._rebase()
            self._push_release_branch(self.branch, force=True)
        elif self.finish_release:
            self._clone(self.full_url, self.release_branch)
            self._get_parts()
            self._finish_release()
            self._push_release_branch(self.release_branch)
        else:
            self._clone(self.full_url, self.branch)
            self._get_previous_parts()
            self._get_parts()
            with open('changelog', 'w') as f:
                f.write("Changelog\n")
            for part in sorted(self._parts,
                               key=lambda x: x in parts_do_not_tag):
                if part in parts_ignore:
                    continue
                self._clone(self._parts[part]['source'])
                tag = self._tag(part)
                try:
                    old_tag = self._previous_parts[part]['source-tag']
                    changelog = self._get_change_log(part, tag, old_tag)
                    if changelog:
                        with open('changelog', 'a') as f:
                            f.write('\n{}:\n'.format(part))
                            f.write(changelog)
                            f.write("\n")
                except KeyError:
                    continue
            self._update_yaml()
            if self._cleanup_release_tags_commands:
                logger.debug("".center(80, '#'))
                logger.debug("# Release tag(s) cleanup command(s):")
                logger.debug("".center(80, '#'))
                for c in self._cleanup_release_tags_commands:
                    logger.debug(c)
            self._push_release_branch(self.branch)

    def _cleanup(self):
        shutil.rmtree(self.CWD, ignore_errors=True)
        os.mkdir(self.CWD)

    def _clone(self, repo, branch=None, target_dir=None, cwd=CWD):
        """Clone project repository."""
        repo_basename = os.path.basename(repo)
        repo_basename = repo_basename.replace('.git', '')
        logger.info("".center(80, '#'))
        cmd = ['git', 'clone', repo]
        if target_dir:
            cmd += [target_dir]
            repo_basename = target_dir
        if branch:
            logger.info("# Cloning {} ({})".format(repo_basename, branch))
            cmd += ['-b', branch]
        else:
            logger.info("# Cloning {}".format(repo_basename))
        logger.info("".center(80, '#'))
        run(cmd, cwd=cwd)
        if not os.path.exists(os.path.join(cwd, repo_basename)):
            logger.error('Unable to clone {}'.format(repo))
            raise SystemExit(1)

    def _get_previous_parts(self):
        cmd = ['git', 'describe', '--abbrev=0', '--tags', '--match', 'v*']
        last_release_tag = run(
            cmd, cwd=self.clone_dir, check=True).stdout.decode().rstrip()
        logger.info("Last release tag: {}".format(last_release_tag))
        cmd = ['git', '--no-pager', 'show', '{}:snap/snapcraft.yaml'.format(
               last_release_tag)]
        previous_snapcraft_file = run(
            cmd, cwd=self.clone_dir, check=True).stdout.decode()
        data = self._yaml.load(previous_snapcraft_file)
        for k, v in data["parts"].items():
            if 'source-tag' in v:
                if 'source' in v:
                    self._previous_parts[k] = v

    def _get_parts(self):
        with open(self._snapcraft_file) as fp:
            self._data = self._yaml.load(fp)
        for k, v in self._data["parts"].items():
            if 'source-tag' in v:
                if 'source' in v:
                    self._parts[k] = v

    def _get_change_log(self, part, new_tag, old_tag):
        repo_basename = os.path.basename(self._parts[part]['source'])
        repo_basename = repo_basename.replace('.git', '')
        cmd = ['git', 'log', '--no-merges', "--pretty=format:+ %s",
               '{}...{}'.format(old_tag, new_tag)]
        return run(cmd, cwd=os.path.join(self.CWD, repo_basename),
                   check=True).stdout.decode()

    def _tag(self, part):
        repo_basename = os.path.basename(self._parts[part]['source'])
        repo_basename = repo_basename.replace('.git', '')
        cmd = ['git', 'describe', '--abbrev=40', '--tags']
        if part not in parts_do_not_tag:
            cmd += ['--match', 'snap-*T*']
        data = run(
            cmd,
            cwd=os.path.join(self.CWD, repo_basename),
            check=True).stdout.decode()
        m = re.search(
            r'(?P<tag>.+?)(?P<additional_commits>\-[0-9]+\-g.{40})?$', data)
        if m:
            last_tag = m.group('tag')
            is_tag_required = (m.group('additional_commits') and
                               part not in parts_do_not_tag)
        else:
            raise SystemExit('Error: no tag found for {}'.format(part))
        if is_tag_required:
            last_tag = self.new_tag
            logger.info("Tag required on {}".format(part))
            self._tag_version(part, self.new_tag)
            self._push_changes(part)
            self._cleanup_release_tags_commands.append(
                'git push --delete {} {}'.format(
                    self._parts[part]['source'], self.new_tag)
            )
        else:
            if part not in parts_do_not_tag:
                logger.info("No new changes on {}".format(part))
            logger.info("{} will be used".format(last_tag))
        self._data["parts"][part]['source-tag'] = last_tag
        return last_tag

    def _tag_version(self, part, new_tag):
        """Tag the code version."""
        repo_basename = os.path.basename(self._parts[part]['source'])
        repo_basename = repo_basename.replace('.git', '')
        run(['git', 'tag', new_tag, '-m', new_tag],
            cwd=os.path.join(self.CWD, repo_basename), check=True)
        logger.info("{} applied on {}".format(new_tag, repo_basename))

    def _push_changes(self, part):
        part_uri = self._parts[part]['source']
        repo_basename = os.path.basename(part_uri).replace('.git', '')
        parse_result = urlparse(part_uri)._replace(
            scheme='git+ssh', netloc=urlparse(self.base_url).netloc)
        part_uri = urlunparse(parse_result)
        logger.info('URL target is {}'.format(part_uri))
        if self.dry_run:
            run(['git', 'push', '--dry-run', part_uri, '--tags'],
                cwd=os.path.join(self.CWD, repo_basename), check=True)
        else:
            logger.info("Pushing changes to origin")
            run(['git', 'push', part_uri, '--tags'],
                cwd=os.path.join(self.CWD, repo_basename), check=True)

    def _update_yaml(self):
        """Update yaml and commit."""
        logger.info("".center(80, '#'))
        logger.info("# Updating parts in {}".format(
            self._snapcraft_file))
        logger.info("".center(80, '#'))
        with open(self._snapcraft_file, 'w') as fp:
            self._yaml.dump(self._data, fp)
        logger.info("".center(80, '#'))
        logger.info("# Updating {} version in {}".format(
            self.snap, self._snapcraft_file))
        logger.info("".center(80, '#'))
        bumpversion_output = run(
            ['bumpversion', self.increment_part, '--allow-dirty', '--list'],
            check=True, cwd=self.clone_dir).stdout.decode()
        new_version = bumpversion_output.splitlines()[-1].replace(
            'new_version=', '')
        logger.info("Bump {} to version {}".format(
            self.snap, new_version))
        run(['git', 'add', '--all'], cwd=self.clone_dir, check=True)
        run(['git', 'commit', '-m', 'Bump version number and tag parts'],
            cwd=self.clone_dir, check=True)

    def _push_release_branch(self, local_branch, force=False):
        if self.dry_run:
            if force:
                run(['git', 'push', '--dry-run', '-f',
                     self.full_url,
                     '{}:{}'.format(local_branch, self.release_branch)],
                    cwd=self.clone_dir, check=True)
                run(['git', 'push', '--dry-run', '-f',
                     self.full_url,
                     '{}:{}'.format(local_branch, self.release_branch),
                     '--tags'],
                    cwd=self.clone_dir, check=True)
            else:
                run(['git', 'push', '--dry-run',
                     self.full_url,
                     '{}:{}'.format(local_branch, self.release_branch)],
                    cwd=self.clone_dir, check=True)
                run(['git', 'push', '--dry-run',
                     self.full_url,
                     '{}:{}'.format(local_branch, self.release_branch),
                     '--tags'],
                    cwd=self.clone_dir, check=True)
        else:
            if force:
                run(['git', 'push', '-f',
                     self.full_url,
                     '{}:{}'.format(local_branch, self.release_branch)],
                    cwd=self.clone_dir, check=True)
                run(['git', 'push', '-f',
                     self.full_url,
                     '{}:{}'.format(local_branch, self.release_branch),
                     '--tags'],
                    cwd=self.clone_dir, check=True)
            else:
                run(['git', 'push',
                     self.full_url,
                     '{}:{}'.format(local_branch, self.release_branch)],
                    cwd=self.clone_dir, check=True)
                run(['git', 'push',
                     self.full_url,
                     '{}:{}'.format(local_branch, self.release_branch),
                     '--tags'],
                    cwd=self.clone_dir, check=True)

    def _rebase(self):
        run(['git', 'rebase', 'origin/{}'.format(self.rebase_branch)],
            cwd=self.clone_dir, check=True)

    def _finish_release(self):
        """Tag, reset parts tags, open next dev version and commit."""
        release_tag = "v{}".format(self._data["version"])
        run(['git', 'tag', release_tag, '-m', release_tag],
            cwd=self.clone_dir, check=True)
        logger.info("{} applied on {}".format(release_tag, self.snap))
        logger.info("".center(80, '#'))
        logger.info("# Updating parts in {}".format(
            self._snapcraft_file))
        logger.info("".center(80, '#'))
        for part in self._parts:
            if part in parts_ignore:
                continue
            self._data["parts"][part]['source-tag'] = ""
        with open(self._snapcraft_file, 'w') as fp:
            self._yaml.dump(self._data, fp)
        logger.info("".center(80, '#'))
        logger.info("# Updating {} version in {}".format(
            self.snap, self._snapcraft_file))
        logger.info("".center(80, '#'))
        bumpversion_output = run(
            ['bumpversion', 'minor', '--allow-dirty', '--list'],
            check=True, cwd=self.clone_dir).stdout.decode()
        new_version = bumpversion_output.splitlines()[-1].replace(
            'new_version=', '')
        logger.info("Bump {} to version {}".format(
            self.snap, new_version))
        run(['git', 'add', '--all'], cwd=self.clone_dir, check=True)
        run(['git', 'commit', '-m', 'Bump version to next dev release'],
            cwd=self.clone_dir, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Tag checkbox content parts and update the release branch"),
    )
    parser.add_argument("repository",
                        help="Specify the git repository", metavar="REPO")
    parser.add_argument("release_branch",
                        help="Specify the git release branch",
                        metavar="RELEASE_BRANCH")
    parser.add_argument("-b", "--branch", default='master',
                        help="Specify the git branch", metavar="BRANCH")
    parser.add_argument("-d", "--dry-run", action='store_true',
                        help="Don't push the changes to remote repositories")
    parser.add_argument("-r", "--rebase_branch",
                        help="Specify the git branch to rebase on",
                        metavar="REBASE_BRANCH")
    parser.add_argument("-f", "--finish", action='store_true',
                        help="Finish the release")
    parser.add_argument("-i", "--increment_part", default='release',
                        help="The part of the version to increase",
                        metavar="INCREMENT_PART")
    parser.add_argument("-u", "--user",
                        help="Specify launchpad user id", metavar="USER")
    parser.add_argument("--credentials",
                        help="Specify launchpad credentials", metavar="CRED")
    args = parser.parse_args()
    Release(args).dance()
