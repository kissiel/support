#!/bin/bash
# Run all tests in various versions of Ubuntu via lxd.
# Use of a local apt-cacher-ng instance and setting MIRROR in /etc/default/lxc
# is strongly recommended, to speed up creation of pristine images:
# MIRROR="http://localhost:3142/archive.ubuntu.com/ubuntu"
set -a

LOG_DIR=lxc-logs
mkdir -p $LOG_DIR

pastebinit() {
    /usr/bin/python /usr/bin/pastebinit "$@";
}

test_lxc_can_run(){
    PROBLEM=0
    if ! which lxc > /dev/null; then
        echo "lxc commands not found, maybe you need to install lxd and lxc"
        PROBLEM=1
    fi
    return $PROBLEM
}


start_lxc_for(){
    target=$1
    distro=$1
    pristine_container=${1}-pristine
    target_container=${1}-testing
    [ "$target" != "" ] || return 1

    # Ensure the target_container isn't still around from a previous run
    lxc delete -f $target_container &> /dev/null

    # Ensure we have a pristine container, create it otherwise.
    if ! lxc ls |grep -q $pristine_container; then
        echo "[$distro] [$(date +%H:%M:%S)] creating pristine container"
        if ! lxc init ubuntu:$target $pristine_container >> $LOG_DIR/$target.init.log 2<&1; then
            outcome=1
            echo "[$distro] [$(date +%H:%M:%S)] Unable to create pristine container!"
            echo "[$distro] output: $(pastebinit $LOG_DIR/$target.init.log)"
            echo "[$distro] NOTE: unable to execute tests, marked as failed"
            echo "[$distro] Trying to destroy to reclaim possible resources"
            lxc stop -f $pristine_container
            return
        fi
    fi
    lxc start $pristine_container >> $LOG_DIR/$target.pristine.log 2<&1
    outcome=$((outcome+$?))
    lxc exec $pristine_container -- rm -f /etc/cron.daily/apt-compat
    lxc exec $pristine_container -- systemctl stop apt-daily.timer
    lxc exec $pristine_container -- systemctl disable --now apt-daily{,-upgrade}.{timer,service}
    # Allow time for the container to activate
    while ! lxc info $pristine_container |grep -qP "eth0:\tinet\t"; do
        sleep 1
    done
    lxc exec $pristine_container apt update >> $LOG_DIR/$target.pristine.log 2<&1
    outcome=$((outcome+$?))
    lxc exec $pristine_container -- bash -c "DEBIAN_FRONTEND=noninteractive apt dist-upgrade -y" >> $LOG_DIR/$target.pristine.log 2<&1
    outcome=$((outcome+$?))
    lxc exec $pristine_container -- bash -c "DEBIAN_FRONTEND=noninteractive apt install -y software-properties-common python3-dev" >> $LOG_DIR/$target.pristine.log 2<&1
    outcome=$((outcome+$?))
    pkg_list="$(cat */requirements/deb-*.txt |grep -v '^#' |uniq | tr '\n' ' ')"
    if [ -n "$pkg_list" ]; then
        lxc exec $pristine_container -- bash -c "DEBIAN_FRONTEND=noninteractive apt install -y $pkg_list" >> $LOG_DIR/$target.pristine.log 2<&1
        outcome=$((outcome+$?))
    fi
    lxc stop $pristine_container >> $LOG_DIR/$target.pristine.log 2<&1
    outcome=$((outcome+$?))
    if [ $outcome -ne 0 ]; then
        echo "[$distro] [$(date +%H:%M:%S)] Unable to update pristine container!"
        echo "[$distro] output: $(pastebinit $LOG_DIR/$target.pristine.log)"
        echo "[$distro] NOTE: unable to execute tests, marked as failed"
        echo "[$distro] Trying to destroy to reclaim possible resources"
        return
    fi

    echo "[$distro] [$(date +%H:%M:%S)] starting container"
    lxc copy -e $pristine_container $target_container
    echo -en "uid $UID 1000\ngid $(id -g) 1000" | lxc config set $target_container raw.idmap -
    lxc config device add $target_container project disk source=$PWD path=/root/project
    if ! lxc start $target_container >> $LOG_DIR/$target.startup.log 2<&1; then
        outcome=1
        echo "[$distro] [$(date +%H:%M:%S)] Unable to start ephemeral container!"
        echo "[$distro] output: $(pastebinit $LOG_DIR/$target.startup.log)"
        echo "[$distro] NOTE: unable to execute tests, marked as failed"
        echo "[$distro] Destroying failed container to reclaim resources"
        lxc stop -f $target_container
        return 1
    fi

    # Wait for networking on the container, or provisioning will fail
    while ! lxc info $target_container |grep -qP "eth0:\tinet\t"; do
        sleep 1
    done

    # Before provisioning, try to detect and configure apt-cacher-ng
    if [ -n "$VAGRANT_APT_CACHE" ]; then
        # Explicitly set
        lxc exec $target_container -- bash -c "echo 'Acquire::http { Proxy \"$VAGRANT_APT_CACHE\"; };' > /etc/apt/apt.conf"
    elif [ -e /etc/apt-cacher-ng ]; then
        # Autodetected local apt-cacher-ng, find out the host IP address to
        # pass into the container
        APT_CACHER_IP=$(ip route get 8.8.8.8 | awk 'NR==1 {print $NF}')
        [ -n "$APT_CACHER_IP" ] && lxc exec $target_container -- bash -c "echo 'Acquire::http { Proxy \"http://$APT_CACHER_IP:3142\"; };' > /etc/apt/apt.conf"
    fi

    # Unlike with Vagrant, we have to provision the VM "manually" here.
    # However we can leverage the same script :D
    echo "[$distro] [$(date +%H:%M:%S)] provisioning container"
    if ! lxc exec $target_container >> $LOG_DIR/$target.pristine.log 2<&1 -- bash -c "/root/project/support/provision-testing-environment /root/project"; then
        echo "[$distro] [$(date +%H:%M:%S)] Unable to provision requirements in container!"
        echo "[$distro] output: $(pastebinit $LOG_DIR/$target.pristine.log)"
        fix_permissions
        echo "[$distro] Destroying failed container to reclaim resources"
        lxc stop -f $target_container
        return 1
    fi
}

fix_permissions(){
    # Fix permissions.
    # provision-testing-environment runs as root and creates a series of
    # root-owned files in the branch directory. Later, tarmac will want
    # to delete these files, so after provisioning we change everything
    # under the branch directory to be owned by the unprivileged user,
    # so stuff can be deleted correctly later.
    echo "[$distro] [$(date +%H:%M:%S)] Fixing file permissions in source directory"
    if ! lxc exec $target_container -- bash -c "chown -R --reference=/root/project/support/test-in-lxc.sh /root/project" >> $LOG_DIR/$target.fix-perms.log 2<&1; then
        echo "[$distro] [$(date +%H:%M:%S)] Unable to fix permissions!"
        echo "[$distro] output: $(pastebinit $LOG_DIR/$target.fix-perms.log)"
        echo "[$distro] Some files owned by root may have been left around, fix them manually with chown."
    fi
}

if [ "$1" = "" ]; then
    # Releases we actually want to test should be included in target_list below.
    target_list="xenial bionic"
else
    target_list="$1"
fi

PASS="$(printf "\33[32;1mPASS\33[39;0m")"
FAIL="$(printf "\33[31;1mFAIL\33[39;0m")"

outcome=0

test_lxc_can_run || exit 1

perform_test(){
    target_release=$1
    if ! start_lxc_for $target_release; then
        outcome=1
        return $outcome
    fi
    # Our actual container has "-testing" appended.
    target=${target_release}-testing
    # Display something before the first test output
    echo "[$distro] [$(date +%H:%M:%S)] Starting tests..."

    # Run test suite commands here.
    # Tests are found in each component's requirements/ dir and are named *container-tests-*
    # Numbers can be used at the beginning to control running order within each component.
    # Tests scripts are expected to:
    # - Be run from the *component's* top-level directory. This is a convenience to avoid
    #   a boilerplate "cd .." on every test script. So for 'plainbox' we do the equivalent of
    #   $ cd $BLAH/plainbox
    #   $
    # - Exit 0 for success, other codes for failure
    # - Write logs/debugging data to stdout and stderr.
    for test_script in $(find ./ -path '*/requirements/*container-tests-*' | sort); do
        echo "[$distro] Found a test script: $test_script"
        test_name=$(basename $test_script)
        # Two dirnames strips the requirements/ component
        component_dir=$(dirname $(dirname $test_script))
        # Inside the LXC container, tests are relative to /root because that's where we mount the project directory
        script_md5sum=$(echo $test_script | md5sum |cut -d " " -f 1)
        logfile=$LOG_DIR/${target}.${test_name}.${script_md5sum}.log
        if lxc exec $target_container -- bash -c 'cd /root/project/'"$component_dir && ./requirements/$test_name" >> $logfile 2<&1
        then
            echo "[$distro] [$(date +%H:%M:%S)] ${test_name}: $PASS"
        else
            outcome=1
            echo "[$distro] [$(date +%H:%M:%S)] ${test_name}: $FAIL"
            echo "[$distro] output: $(pastebinit $logfile)"
        fi
    done

    fix_permissions

    echo "[$distro] [$(date +%H:%M:%S)] Destroying container"
    # Stop the container first
    if ! lxc stop -f $target_container >> $LOG_DIR/$target.stop.log 2<&1; then
        echo "[$distro] [$(date +%H:%M:%S)] Unable to stop container!"
        echo "[$distro] output: $(pastebinit $LOG_DIR/$target.stop.log)"
        echo "[$distro] You may need to manually 'lxc stop -f $target_container' to fix this"
    fi
    return $outcome
}
outcome=0

# spawn one lxc process per target from $target_list
# if any of them fails, $outcome will be set to 1
echo $target_list |xargs -P0 -n1 bash -c 'perform_test "$@"' _ || outcome=1
# Propagate failure code outside
exit $outcome
