#!/bin/bash

set -e

mkdir -p ${1:-"checkbox-project"} && cd ${1:-"checkbox-project"}
git clone git+ssh://git.launchpad.net/~checkbox-dev/checkbox/+git/support
git clone git+ssh://git.launchpad.net/plainbox
git clone git+ssh://git.launchpad.net/checkbox-ng
git clone git+ssh://git.launchpad.net/checkbox-support
ln -s support/light-venv mk-venv
