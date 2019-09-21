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

import argparse
import configparser
import glob
import json
import logging
import os
import shutil
import subprocess


projects = (
    'checkbox-support',
    'checkbox-ng',
    'plainbox-provider-resource',
    'plainbox-provider-checkbox',
    'plainbox-provider-tpm2',
    'plainbox-provider-ipdt',
    'checkbox-provider-phoronix',
    'plainbox-provider-sru',
    'plainbox-provider-certification-client',
    'plainbox-provider-certification-server',
)


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
fh = logging.FileHandler('release.log')
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
        self.project = args.project
        self.step = args.step
        self.user = args.user
        self.target_user = args.target_user
        self.config = json.load(open(args.config))
        self.dry_run = self.config['dry_run']
        self.config_file = args.config

        self.packaging = 'packaging_{}'.format(self.project)

        self.base_url = 'git+ssh://{}@git.launchpad.net'.format(self.user)
        self.full_url = os.path.join(self.base_url, self.project)
        self.snap = os.path.basename(self.full_url)

        self.clone_dir = os.path.join(self.CWD, self.project)
        self.packaging_clone_dir = os.path.join(self.CWD, self.packaging)

    def run(self):
        """Release step selector."""
        if not self.config[self.project]:
            return
        if self.step == 'clone':
            logger.info("".center(80, '#'))
            logger.info("# Cloning {} repositories...".format(self.project))
            logger.info("".center(80, '#'))
            self.clone(self.CWD)
        if self.step == 'bump':
            self.bump_version()
            logger.info("".center(80, '#'))
            logger.info("# Bump {} to version {}".format(
                self.project, self.new_version))
            logger.info("".center(80, '#'))
        if self.step == 'sdist':
            logger.info("".center(80, '#'))
            logger.info("# Create {} source tarball...".format(self.project))
            logger.info("".center(80, '#'))
            self.create_source_tarball()
        if self.step == 'dpm':
            logger.info("".center(80, '#'))
            logger.info("# Do the git-dpm dance ({})...".format(self.project))
            logger.info("".center(80, '#'))
            self._prepare_debian_tarball()
            self.dance()
        if self.step == 'open':
            logger.info("".center(80, '#'))
            logger.info("# Open {} next version for development...".format(
                self.project))
            logger.info("".center(80, '#'))
            # Open for development if we did a stable release
            if self.config['mode'] == 'stable':
                self.open_for_development()
        if self.step == 'push':
            logger.info("".center(80, '#'))
            logger.info("# Push {} code and packaging repositories "
                        "to Launchpad...".format(self.project))
            logger.info("".center(80, '#'))
            self.push()
        if self.step == 'merge':
            logger.info("".center(80, '#'))
            logger.info("# Merge the {} release branch into master...".format(
                self.project))
            logger.info("".center(80, '#'))
            if self.config['mode'] == 'stable':
                self.merge()
        if self.step == 'build':
            logger.info("".center(80, '#'))
            logger.info("# Update {} {} PPA recipe and kick-off the builds".format(self.project, self.config['mode']))
            logger.info("".center(80, '#'))
            self.build()

    @staticmethod
    def cleanup():
        shutil.rmtree(Release.CWD, ignore_errors=True)
        os.mkdir(Release.CWD)

    def clone(self, cwd):
        """Clone code and packaging repositories."""
        logger.info('# Cloning {}/~{}/{}'.format(
            self.base_url, self.target_user, self.project))
        run(['git', 'clone', '{}/~{}/{}'.format(
            self.base_url, self.target_user, self.project)], cwd=cwd)
        logger.info('# Cloning {}/~{}/{}/+git/packaging'.format(
            self.base_url, self.target_user, self.project))
        run(['git', 'clone', '{}/~{}/{}/+git/packaging'.format(
            self.base_url, self.target_user, self.project), self.packaging],
            cwd=cwd)
        for path in [self.clone_dir, self.packaging_clone_dir]:
            if not os.path.exists(path):
                logger.error('Unable to clone {}'.format(path))
                raise SystemExit(1)
        # Fetch the release branch if it exists
        run(['git', 'fetch', 'origin', 'release:release'], cwd=self.clone_dir)
        # Update origin url if needed
        if self.target_user != self.user:
            url = run(['git', 'remote', 'get-url', 'origin'],
                      cwd=self.clone_dir, check=True).stdout.decode().rstrip()
            url = url.replace(self.user, self.target_user)
            run(['git', 'remote', 'set-url', 'origin', url],
                cwd=self.clone_dir, check=True)
        # Checkout the release branch
        process = run(['git', 'checkout', '-b', 'release'], cwd=self.clone_dir)
        if process.returncode:
            run(['git', 'checkout', 'release'], cwd=self.clone_dir, check=True)
        is_release_required = self.is_release_required
        if is_release_required:
            logger.info("Release required: {}".format(is_release_required))
        else:
            self.config[self.project] = is_release_required
            with open(self.config_file, "w") as f:
                json.dump(self.config, f, indent=4, sort_keys=True)
            logger.info("Release required: {} (Json config updated!)".format(
                is_release_required))

    @property
    def is_release_required(self):
        """Check both code and packaging repos for new commits."""
        count = run(
            ['git', 'rev-list', '--left-only', '--count', 'master...release'],
            cwd=self.clone_dir).stdout.decode().rstrip()
        logger.debug(
            'Release branch is {} commit(s) behind local master'.format(count))
        try:
            commits_behind = int(count)
        except TypeError:
            commits_behind = 0
        if commits_behind:
            run(['git', 'merge', 'master', '-s', 'recursive', '-Xtheirs'],
                cwd=self.clone_dir, check=True)
            return True
        version_pattern = '*'
        if self.config['mode'] == 'stable':
            version_pattern = '*[^c][0-9]'  # Up to 9 RC :)
        code_change = run(
            'git diff $(git describe --abbrev=0 --tags --match '
            '"v{}") -- . ":(exclude).*ignore"'.format(version_pattern),
            shell=True, cwd=self.clone_dir, check=True).stdout
        version_change = run(
            'git diff $(git describe --abbrev=0 --tags --match "v{}")'
            ' -G "__version__|current_version|version="'.format(
                version_pattern),
            shell=True, cwd=self.clone_dir, check=True).stdout
        if code_change == version_change:
            code_change = False
        packaging_change = run(
            'git diff $(git describe --abbrev=0 --tags --match '
            '"debian-{}") --name-only'.format(version_pattern),
            shell=True, cwd=self.packaging_clone_dir, check=True).stdout
        if code_change or packaging_change:
            return True
        else:
            return False

    def _get_version(self):
        config = configparser.ConfigParser()
        try:
            config.read(os.path.relpath(self.clone_dir+'/.bumpversion.cfg'))
            return config['bumpversion']['current_version']
        except KeyError:
            logger.error("{} .bumpversion.cfg not found".format(self.project))
            raise SystemExit(1)

    def _save_versions(self):
        try:
            with open('versions.json') as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        last_stable_version = run(
            ['git', 'describe', '--abbrev=0',
             '--tags', '--match', 'v*[^c][0-9]'],
            cwd=self.clone_dir, check=True).stdout.decode().rstrip()
        data[self.project] = {
            'last_stable': last_stable_version[1:],
            'current': self.current_version,
            'new': self.new_version
        }
        with open('versions.json', 'w') as f:
            json.dump(data, f, indent=4, sort_keys=True)

    def bump_version(self):
        """Bump project version and tag according to release mode."""
        self.current_version = self._get_version()
        bumpversion_output = ''
        # Several calls to bumpversion are required until
        # https://github.com/peritus/bumpversion/issues/50 is solved
        # (Allow the part to be defined on the command line)
        if self.config['mode'] == 'stable':
            if 'dev' in self.current_version:
                # bump to jump to rc0
                run(['bumpversion', 'release', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True)
                # bump to jump to final
                bumpversion_output = run(
                    ['bumpversion', 'release', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True).stdout.decode()
            elif 'rc' in self.current_version:
                # bump to jump to final
                bumpversion_output = run(
                    ['bumpversion', 'release', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True).stdout.decode()
            else:
                # bump to jump to dev0
                run(['bumpversion', 'minor', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True)
                # bump to jump to rc0
                run(['bumpversion', 'release', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True)
                # bump to jump to final
                bumpversion_output = run(
                    ['bumpversion', 'release', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True).stdout.decode()
        else:
            if 'dev' in self.current_version:
                # bump to jump to rc0
                run(['bumpversion', 'release', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True)
                # bump to jump to rc1
                bumpversion_output = run(
                    ['bumpversion', 'N', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True).stdout.decode()
            elif 'rc' in self.current_version:
                # bump to jump to rc(N+1)
                bumpversion_output = run(
                    ['bumpversion', 'N', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True).stdout.decode()
            else:
                # bump to jump to dev0
                run(['bumpversion', 'minor', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True)
                # bump to jump to rc0
                run(['bumpversion', 'release', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True)
                # bump to jump to rc1
                bumpversion_output = run(
                    ['bumpversion', 'N', '--allow-dirty', '--list'],
                    cwd=self.clone_dir, check=True).stdout.decode()
        self.new_version = bumpversion_output.splitlines()[-1].replace(
            'new_version=', '')
        run(['git', 'add', '--all'], cwd=self.clone_dir, check=True)
        run(['git', 'commit', '-m', 'Bump to v'+self.new_version],
            cwd=self.clone_dir, check=True)
        self._save_versions()
        # Tag the code version
        run(['git', 'tag', 'v'+self.new_version,
            '-m', 'Release {} v{}'.format(project, self.new_version)],
            cwd=self.clone_dir, check=True)
        return self.new_version

    @staticmethod
    def changelog():
        """Create the changelog for released projects."""
        versions = json.load(open('versions.json'))
        logger.info("".center(80, '#'))
        logger.info("# Create the changelog...")
        logger.info("".center(80, '#'))
        for p in projects:
            try:
                project = versions[p]
                clone_dir = os.path.join(Release.CWD, p)
                old_tag = 'v' + project['last_stable']
                new_tag = 'v' + project['new']
                cmd = ['git', 'log', '--no-merges', "--pretty=format:+ %s",
                       '{}...{}'.format(old_tag, new_tag)]
                log = run(cmd, cwd=clone_dir, check=True).stdout.decode()
                if log:
                    logger.debug("# {}: {}".format(p, cmd))
                    with open('changelog', 'a') as f:
                        f.write('\n{}:\n'.format(p))
                        f.write(log)
                        f.write("\n")
            except KeyError:
                continue

    def create_source_tarball(self):
        """Create and sign the project source tarball."""
        self.new_version = self._get_version()
        if os.path.exists(os.path.relpath(self.clone_dir+'/manage.py')):
            run(['./manage.py', 'sdist'], cwd=self.clone_dir, check=True)
        elif os.path.exists(os.path.relpath(self.clone_dir+'/setup.py')):
            run(['./setup.py', 'sdist'], cwd=self.clone_dir, check=True)

    def open_for_development(self):
        """Bump the project version to open a new release for development."""
        bumpversion_output = run(
            ['bumpversion', 'minor', '--allow-dirty', '--list'],
            cwd=self.clone_dir, check=True).stdout.decode()
        new_dev_version = bumpversion_output.splitlines()[-1].replace(
            'new_version=', '')
        run(['git', 'add', '--all'], cwd=self.clone_dir, check=True)
        run(['git', 'commit', '-m', 'increment version to v'+new_dev_version],
            cwd=self.clone_dir, check=True)
        logger.info("# Bump {} to version {}".format(
                self.project, new_dev_version))

    def _prepare_debian_tarball(self):
        """Copy the project release tarball to a debian suitable name."""
        self.new_version = self._get_version()
        self.debian_new_version = self.new_version.replace('rc', '~rc')
        archives = glob.glob('{}/dist/*{}.tar.gz'.format(
            self.clone_dir, self.new_version))
        deb_project = self.project
        if self.project == 'plainbox-provider-resource':
            deb_project = 'plainbox-provider-resource-generic'
        self.orig_tarball = '{}_{}.orig.tar.gz'.format(
            deb_project, self.debian_new_version)
        try:
            self.tarball = archives[-1]
            shutil.copyfile(self.tarball,
                            os.path.join(self.CWD, self.orig_tarball))
        except (IndexError, FileNotFoundError):
            logger.error("{} sdist tarball not found".format(self.project))
            raise SystemExit(1)

    def dance(self):
        """Update the packaging repo with git-dpm."""
        run(['git-dpm', 'import-new-upstream',
            os.path.join('..', self.orig_tarball)],
            cwd=self.packaging_clone_dir, check=True)
        run(['pristine-tar', 'commit',
            os.path.join('..', self.orig_tarball)],
            cwd=self.packaging_clone_dir, check=True)
        run(['git-dpm', 'prepare'], cwd=self.packaging_clone_dir, check=True)
        run(['git-dpm', 'rebase-patched'],
            cwd=self.packaging_clone_dir, check=True)
        run(['git-dpm', 'dch', '--', '-v', self.debian_new_version+'-1',
            '-D', 'UNRELEASED', '"new upstream version"'],
            cwd=self.packaging_clone_dir, check=True)
        run(['git-dpm', 'status'], cwd=self.packaging_clone_dir, check=True)
        run(['git-dpm', 'tag'], cwd=self.packaging_clone_dir, check=True)

    def push(self):
        """Push code and packaging repositories to Launchpad."""
        if self.dry_run:
            run(['git', 'push', '--dry-run', '{}/~{}/{}'.format(
                self.base_url, self.target_user, self.project),
                'release', '--tags'], cwd=self.clone_dir, check=True)
            run(['git', 'push', '--dry-run', '{}/~{}/{}/+git/packaging'.format(
                self.base_url, self.target_user, self.project), '--all'],
                cwd=self.packaging_clone_dir, check=True)
            run(['git', 'push', '--dry-run', '{}/~{}/{}/+git/packaging'.format(
                self.base_url, self.target_user, self.project), '--tags'],
                cwd=self.packaging_clone_dir, check=True)
        else:
            run(['git', 'push', '{}/~{}/{}'.format(
                self.base_url, self.target_user, self.project),
                'release', '--tags'], cwd=self.clone_dir, check=True)
            run(['git', 'push', '{}/~{}/{}/+git/packaging'.format(
                self.base_url, self.target_user, self.project), '--all'],
                cwd=self.packaging_clone_dir, check=True)
            run(['git', 'push', '{}/~{}/{}/+git/packaging'.format(
                self.base_url, self.target_user, self.project), '--tags'],
                cwd=self.packaging_clone_dir, check=True)

    def merge(self):
        """Merge the release branch into master."""
        if self.dry_run:
            run(['git', 'push', '--dry-run', '{}/~{}/{}'.format(
                self.base_url, self.target_user, self.project),
                '--delete', 'release'], cwd=self.clone_dir, check=True)
        else:
            output = run("./support/release/git/lp-propose-merge ~{}/{} -s --merged-timeout 3600 --credentials $HOME/.hwcert_creds/credentials".format(self.target_user, self.project), shell=True, check=True).stdout.decode()
            logger.info(output)
            # Delete the release branch once merged into master
            run(['git', 'push', '{}/~{}/{}'.format(
                self.base_url, self.target_user, self.project),
                '--delete', 'release'], cwd=self.clone_dir, check=True)

    def build(self):
        """Update the PPA recipe and kick-off the builds."""
        versions = json.load(open('versions.json'))
        try:
            new_version = versions[self.project]['new']
        except KeyError:
            logger.warning('# Skipping {} build step'.format(self.project))
            return
        if self.dry_run:
            logger.info('# Dry run: Skipping {} {} build step'.format(
                self.project, new_version))
        else:
            if self.config['mode'] == 'testing':
                output = run(
                    "./support/release/git/lp-recipe-update-build {} --recipe {} -n {} --credentials $HOME/.hwcert_creds/credentials".format(
                        self.project, self.project+'-testing', new_version), shell=True, check=True).stdout.decode().rstrip()
            else:
                output = run(
                    "./support/release/git/lp-recipe-update-build {} --recipe {} -n {} --credentials $HOME/.hwcert_creds/credentials".format(
                        self.project, self.project+'-stable', new_version), shell=True, check=True).stdout.decode().rstrip()
            logger.info(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Manage checkbox debian package releases")
    parser.add_argument("--project",
                        help="Specify the checkbox project", metavar="PROJECT")
    parser.add_argument("--step", required=True,
                        help="run a specific release step", metavar="STEP")
    parser.add_argument("-u", "--user",
                        help="Specify launchpad user id", metavar="USER")
    parser.add_argument(
        '--target-user', default='checkbox-dev',
        help=("target repositories owner (default: %(default)s)"))
    parser.add_argument("--credentials",
                        help="Specify launchpad credentials", metavar="CRED")
    parser.add_argument("--config", required=True,
                        help="Specify release settings", metavar="CONFIG")
    args = parser.parse_args()
    if args.step == 'cleanup':
        Release.cleanup()
    elif args.step == 'changelog':
        Release.changelog()
    else:
        if args.project:
            Release(args).run()
        else:
            for project in projects:
                args.project = project
                Release(args).run()
