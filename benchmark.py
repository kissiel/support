#!/usr/bin/env python3
# Copyright 2020 Canonical Ltd.
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
"""
Benchmark Checkbox with different scenarios.

This program runs a particular launcher and measures how long it took to run
it. You can run this script from anywhere, just use checkbox-ng's path as the
argument. If you don't have access to the Internet from where the script is run
then use --provider-repository option to point to a benchmarking-provider git
tree.
"""

import argparse
import contextlib
import glob
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from pprint import pprint


def prepare_venv(venv_path):
    """Create venv and develop the benchmarking provider in it."""
    os.chdir('checkbox-ng')
    subprocess.run(
        ['./mk-venv', venv_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    manage_py = os.path.join('benchmarking-provider', 'manage.py')
    os.chdir('..')
    subprocess.run(
        ". {}; {} develop -d $PROVIDERPATH".format(
            os.path.join('venv', 'bin', 'activate'), manage_py),
        shell=True, stdout=subprocess.DEVNULL, check=True)


def run_via_remote(launcher):
    """Launch a slave and run `launcher` via master on that slave."""
    try:
        slave_proc = subprocess.Popen(
            '. venv/bin/activate; checkbox-cli slave',
            shell=True, start_new_session=True)
    except subprocess.CalledProcessError:
        raise SystemExit("Failed to run the slave")
    with contextlib.ExitStack() as stack:
        def kill_slave(*_):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(slave_proc.pid), signal.SIGTERM)
        stack.push(kill_slave)
        try:
            start = time.time()
            subprocess.run(
                ". venv/bin/activate; checkbox-cli master localhost {}".format(
                    launcher), shell=True,
                stderr=subprocess.STDOUT, check=True)
            stop = time.time()
        except subprocess.CalledProcessError as exc:
            print(exc.stdout.decode(sys.stdout.encoding))
            raise SystemExit("Failed to remotely run launcher {}".format(
                launcher))
        if slave_proc.poll() is not None:
            raise SystemExit("Slave died by its own. Benchmarking failed")
    return stop - start


def run_locally(launcher):
    """Launch given launcher locally."""
    try:
        start = time.time()
        subprocess.run(". venv/bin/activate; checkbox-cli {}".format(
            launcher), shell=True, stderr=subprocess.STDOUT, check=True)
        stop = time.time()
    except subprocess.CalledProcessError as exc:
        print(exc.stdout.decode(sys.stdout.encoding))
        raise SystemExit("Failed to remotely run launcher {}".format(launcher))
    return stop - start


def main():
    """Entry point."""
    parser = argparse.ArgumentParser("Checkbox benchmark")
    parser.add_argument(
        'checkbox_path', help='Path to where Checkbox can be found')
    parser.add_argument(
        '--provider-repository',
        help='Location of the benchmarking provider repository',
        default=(
            'https://git.launchpad.net/~checkbox-dev/'
            'checkbox/+git/benchmarking-provider'))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='cbox-bench') as tmpdir:
        os.chdir(tmpdir)
        shutil.copytree(
            args.checkbox_path, 'checkbox-ng')
        subprocess.run(
            ['git', 'clone', args.provider_repository,
             'benchmarking-provider'], check=True)
        launchers = glob.glob('benchmarking-provider/launcher-*')
        scenarios = [s.replace(
            'benchmarking-provider/launcher-', '') for s in launchers]
        results = dict()
        prepare_venv(os.path.join(tmpdir, 'venv'))
        for scenario in scenarios:
            local_result = run_locally(os.path.join(
                tmpdir, 'benchmarking-provider',
                'launcher-{}'.format(scenario)))
            remote_result = run_via_remote(os.path.join(
                tmpdir, 'benchmarking-provider',
                'launcher-{}'.format(scenario)))
            results['local-{}'.format(scenario)] = local_result
            results['remote-{}'.format(scenario)] = remote_result
        pprint(results)


if __name__ == '__main__':
    main()
