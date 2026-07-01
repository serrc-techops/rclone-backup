#!/usr/bin/python3

import glob
import os
import shutil
import subprocess
import sys


SCRIPT_NAME = "rclone-backup"
CONFIG_DIR = "/srv/rclone"
LOG_FILE_GLOB = "/var/log/rclone-backup.log*"
RCLONE_REMOTE_NAME = "googledrive"

CRON_FILES = [
    "/etc/cron.d/rclone-backup",
]

TEMP_BACKUP_GLOBS = [
    "/tmp/*_dhcp_backup_*.tar.gz",
    "/tmp/*_dns_backup_*.tar.gz",
    "/tmp/*_gorillamanifests_backup_*.tar.gz",
    "/tmp/*_munkimanifests_backup_*.tar.gz",
    "/tmp/*_unifi_backup_*.tar.gz",
]


def remove_path(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.exists(path) or os.path.islink(path):
            os.remove(path)
    except Exception:
        pass


def remove_rclone_remote():
    try:
        if shutil.which("rclone"):
            subprocess.run(
                ["rclone", "config", "delete", RCLONE_REMOTE_NAME],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    except Exception:
        pass


def remove_root_crontab_entries():
    script_path = os.path.abspath(__file__)

    markers = [
        SCRIPT_NAME,
        script_path,
        CONFIG_DIR,
    ]

    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return

        lines = result.stdout.splitlines()

        kept_lines = [
            line for line in lines
            if not any(marker in line for marker in markers)
        ]

        if kept_lines != lines:
            subprocess.run(
                ["crontab", "-"],
                input="\n".join(kept_lines) + "\n",
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

    except Exception:
        pass


def main():
    current_script = os.path.abspath(__file__)

    remove_rclone_remote()
    remove_root_crontab_entries()

    for cron_file in CRON_FILES:
        remove_path(cron_file)

    for path in glob.glob(LOG_FILE_GLOB):
        remove_path(path)

    for pattern in TEMP_BACKUP_GLOBS:
        for path in glob.glob(pattern):
            remove_path(path)

    remove_path(CONFIG_DIR)
    remove_path(current_script)

    sys.exit(0)


if __name__ == "__main__":
    main()
