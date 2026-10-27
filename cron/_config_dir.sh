#!/usr/bin/env bash
# _config_dir.sh -- the ONE resolver for every per-user file the cron scripts
# read or write. Sourced, never executed. Mirrors install.sh's ENV_FILE rule:
# every per-user file lives in the package default directory (WIZARD_CONFIG_DIR
# overrides it).
#
# LAUNCHD_PREFIX lives here too and MUST equal install.sh's LAUNCHD_PREFIX;
# voidloom/tests/test_launchd_prefix_agrees.py asserts the two literals agree.
CONFIG_DIR_DEFAULT="${WIZARD_CONFIG_DIR:-${HOME}/.voidloom}"
LAUNCHD_PREFIX="voidloom"

_config_path() {
    # $1 = basename. Prints the path to use for that file.
    echo "${CONFIG_DIR_DEFAULT}/$1"
}

_config_origin() {
    # $1 = basename. Prints WHERE _config_path found it, for a status line.
    if [ -e "${CONFIG_DIR_DEFAULT}/$1" ]; then echo "package default"
    else echo "package default (not yet present)"; fi
}
