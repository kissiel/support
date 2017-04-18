#!/usr/bin/env python3
# This file is part of Checkbox.
#
# Copyright 2017 Canonical Ltd.
# Written by:
#   Maciej Kisielewski <maciej.kisielewski@canonical.com>
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

# This tool prints the differences between two providers (or two revisions of
# the same provider) in terms of job lists defined in those providers.
# Two comparisons are made, one using `checkbox-cli list all-jobs`, and the
# other using list-bootstrapped with every test plan found
# Usage: python3 provider_diff.py plan.yaml
# where plan.yaml specifies what should be compared using following grammar:
#
# before:                 # settings for the the first compared provider
#   source:               # URL to the provider (used in git clone)
#   source-subdir:        # (optional) dir with the provider in the above repo
#   source-commit:        # which revision to use (used in git checkout)
# after:
#   source:               # same function as in `before`
#   source-subdir:        # same function as in `before`
#   source-commit:        # same function as in `before`
# additional-providers:   # (optional) lists other providers used in testing
#   my-added-provider:    # name to use when creating temporary dirs
#       source:           # same as in before and after
#       source-subdir:    # same as in before and after

import argparse
import os
import subprocess
import sys
import tempfile
import yaml


def cmd(command, venv=None):
    if venv:
        command = '. venv-{}/bin/activate && '.format(venv) + command
    return subprocess.check_output(command, shell=True)


def setup(config):
    # mkdtemp instead of TemporaryDirectory, so we control deletion
    tdir = tempfile.mkdtemp(dir=os.path.abspath(os.curdir))
    print("Using {} as the working directory".format(tdir))
    os.chmod(tdir, 0o755)
    # pull before
    os.chdir(tdir)
    subprocess.check_call(
        ['git', 'clone', config['before']['source'], 'before'])
    os.chdir(os.path.join(
        tdir, 'before', config['before'].get('source-subdir', '.')))
    prov_installers_before = [
        os.path.join(os.path.abspath(os.curdir), 'manage.py')]
    subprocess.check_call(
        ['git', 'checkout', config['before']['source-commit']])
    # pull after
    os.chdir(tdir)
    subprocess.check_call(
        ['git', 'clone', config['after']['source'], 'after'])
    os.chdir(os.path.join(
        tdir, 'after', config['after'].get('source-subdir', '.')))
    prov_installers_after = [
        os.path.join(os.path.abspath(os.curdir), 'manage.py')]
    subprocess.check_call(
        ['git', 'checkout', config['after']['source-commit']])
    os.chdir(tdir)
    subprocess.check_call(['../bootstrap-checkbox.sh'])
    # get additional providers
    if config.get('additional_providers'):
        for prov in config.get('additional_providers', []):
            for name, params in prov.items():
                subprocess.check_call(
                    ['git', 'clone', params['source'], name])
                manage_py_path = os.path.abspath(os.path.join(
                    name, params.get('source-subdir', '.'), 'manage.py'))
                prov_installers_before.append(manage_py_path)
                prov_installers_after.append(manage_py_path)
    # create venvs
    os.chdir('checkbox-project')
    subprocess.check_call(['./mk-venv', 'venv-before'])
    for manage_py in prov_installers_before:
        cmd('{} develop -d $PROVIDERPATH'.format(manage_py), 'before')
    subprocess.check_call(['./mk-venv', 'venv-after'])
    for manage_py in prov_installers_after:
        cmd('{} develop -d $PROVIDERPATH'.format(manage_py), 'after')


def main():
    parser = argparse.ArgumentParser("Plainbox Provider Comparison Tool")
    parser.add_argument(
        'configuration', nargs='?', default='provider_diff.yaml')
    args = parser.parse_args()
    if not os.path.isfile(args.configuration):
        raise SystemExit("Configuration file '{}' not found!".format(
            args.configuration))
    with open(args.configuration, 'rt') as f:
        config = yaml.load(f)
    setup(config)
    # compare static definition list for all jobs
    before_defs = get_job_definitions('before')
    after_defs = get_job_definitions('after')
    print("Comparing static job list")
    compare_sets(before_defs, after_defs)
    before_tps = get_test_plans('before')
    after_tps = get_test_plans('after')
    print("Comparing test plans")
    compare_sets(before_tps, after_tps)
    if before_tps == after_tps:
        for tp in before_tps:
            print("Comparing bootstrapped test plan {}".format(tp))
            compare_sets(
                list_bootstrapped('before', tp),
                list_bootstrapped('after', tp))


def compare_sets(before, after):
    only_in_before = [x for x in before if x not in after]
    only_in_after = [x for x in after if x not in before]
    if not only_in_before and not only_in_after:
        print("No differences")
        return
    if only_in_before:
        print("Found only in the 'before' commit:")
        print('\t' + '\n\t'.join(only_in_before))
    if only_in_after:
        print("Found only in the 'after' commit:")
        print('\t' + '\n\t'.join(only_in_after))


def get_job_definitions(venv):
    out = cmd(r'checkbox-cli list all-jobs -f "{id}\n"', venv)
    return out.decode(sys.stdout.encoding).split('\n')


def get_test_plans(venv):
    out = cmd('checkbox-cli list "test plan"', venv)
    test_plans = []
    for line in out.decode(sys.stdout.encoding).split('\n'):
        if line.startswith('test plan'):
            # valid line looks like this:
            # test plan '2000.foo.bar::tp-name'
            # let's grab a string betweeen the last to apostrophes
            test_plans.append(line.rsplit("'")[-2])
    return test_plans


def list_bootstrapped(venv, tp):
    out = cmd('checkbox-cli list-bootstrapped {}'.format(tp), venv)
    return [line for line in out.decode(sys.stdout.encoding).split('\n')]


if __name__ == '__main__':
    main()
