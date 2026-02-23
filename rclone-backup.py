#!/usr/bin/python3

VERSION = '1.066'

# Version 1.062 - Added random sleep up to 20 minutes to prevent multiple devices simultaneously writing to gsheet log
# Version 1.063 - Added nosleep and noupdate arguments to assist in troubleshooting
# Version 1.064 - Added timestamp to logging, set log rotation time interval so new log wouldn't be created after script self-update
# Version 1.065 - Changed dependency installer to pip instead of apt for Google API
# Version 1.066 - Added new autoupdate path for Unifi Network Server

# Arguments:
#    --nosleep : skip sleep when script executed
#    --noupdate : skip automatically updating script contents

import argparse
import hashlib
import os
import random
import subprocess
import shutil
import stat
import sys
import time
import json
from datetime import datetime

# Number of compressed backups to retain
BACKUP_KEEP_COUNT = 5

# Share a single local log file for all backup types
LOG_FILE = "/var/log/rclone-backup.log"

# Number of local log files to retain
MAX_LOG_ARCHIVES = 10

# Rotate logs if LOG_FILE more than 24 hours old
LOG_ROTATE_INTERVAL = 24 * 60 * 60

# Name of pre-configured Rclone remote
RCLONE_REMOTE_NAME = 'googledrive'

# IDs of the individual Google Sheets used for logging and monitoring
DHCP_SPREADSHEET_ID = '1QWORlX7No7FN2woNCmrf4xmtI1cRNC3pxHpk7auXxuE'
DNS_SPREADSHEET_ID = '1jmRT9r900HD-MVYCq-JYwl2p8aIcdsqrqivnomvMFG8'
GORILLAMANIFESTS_SPREADSHEET_ID = '1y4wpZvrD9_f9t5NypY-JWjAWBjXGbsJ5fEB3pqelRo4'
MUNKIMANIFESTS_SPREADSHEET_ID = '1mGkoHwadX2aMW7UKjJGKmAkmfXZWd3Ark5rixv6dvgw'
UNIFI_SPREADSHEET_ID = '1FOAeJrO_NBspwAZGf4_kRTnzh0dXCIcXzhBcDQSHoew'

# URL where the current version of this script can be found 
#   used for auto-updating the script as changes are made
REMOTE_SCRIPT_URL = "https://raw.githubusercontent.com/serrc-techops/rclone-backup/refs/heads/main/rclone-backup.py"


def get_backup_source(backup_type):
    """Return list of directories to backup depending on the backup type"""
    if backup_type == "dhcp":
        return [ "/etc/dhcp" ]
    elif backup_type == "dns":
        return [ "/etc/bind", "/var/cache/bind", "/var/lib/bind" ]
    elif backup_type == "gorillamanifests":
        return [ "/srv/www/gorilla/manifests" ]
    elif backup_type == "munkimanifests":
        return [ "/srv/www/munki/manifests" ]
    elif backup_type == "unifi":
        
        # Used to check for recent update
        one_week_ago = time.time() - (7 * 24 * 60 * 60)

        # Multiple Unifi autobackup locations possible
        unifi_autobackup_paths = [
            "/var/lib/unifi/backup/autobackup",       # Software Controller
            "/data/unifi/data/backup/autobackup",     # UniFi Cloud Key Gen1/Gen2 - newer firmware
            "/srv/unifi/data/backup/autobackup",      # UniFi Cloud Key Gen1/Gen2 - older firmware
            "/usr/lib/unifi/data/backup/autobackup",  # Alternative UniFi installation
            "/opt/unifi/data/backup/autobackup",      # Custom installations
            "/home/uosserver/.local/share/containers/storage/volumes/uosserver_var_lib_unifi/_data/backup", # Unifi Network Server (Podman)
        ]
        try:
            for path in unifi_autobackup_paths:
                if os.path.exists(path):
                    files = os.listdir(path)
                    for file in files:
                        if file.endswith(".unf"):
                            file_path = os.path.join(path, file)
                            # Check if the file is no older than one week
                            file_mod_time = os.path.getmtime(file_path)
                            if file_mod_time >= one_week_ago:
                                return [ path ]
            return "[ERROR] No autobackup path with backups less than seven days old detected. Exiting."
        except Exception as e:
            return f"[ERROR] detecting autobackup failure: {e}"
    else:
        return None


def check_command(command):
    """Check if a command is available and install it if missing."""
    if not shutil.which(command):
        log(f"[INFO] {command} is missing. Installing...")
        subprocess.run(["apt", "update"], check=True)
        subprocess.run(["apt", "install", command, "-y"], check=True)


def pip_install(pkg):
    """Install package via pip."""

    # remove the EXTERNALLY-MANAGED directory if exists
    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    dir_path = f"/usr/lib/{python_version}/EXTERNALLY-MANAGED"
    if os.path.exists(dir_path):
        try:
            if os.path.isdir(dir_path):
                shutil.rmtree(dir_path)
            elif os.path.isfile(dir_path):
                os.remove(dir_path)
            elif os.path.islink(dir_path):
                os.remove(dir_path)
        except Exception as e:
            log(f"[ERROR] removing {dir_path}: {e}")

    try:
        # Check if the package is already installed
        subprocess.check_call([shutil.which('pip'), 'show', pkg])
        print(f"[INFO] {pkg} is already installed.")
    except subprocess.CalledProcessError:
        log(f"[INFO] {pkg} is missing. Installing...")
        subprocess.run(["pip", "install", pkg], check=True)


def rotate_logs():
    """Rotate logs to maintain a fixed number of files."""
    if os.path.exists(LOG_FILE):
        file_age = time.time() - os.path.getmtime(LOG_FILE)
        if file_age >= LOG_ROTATE_INTERVAL:
            for i in range(MAX_LOG_ARCHIVES - 1, 0, -1):
                old_log = f"{LOG_FILE}.{i}"
                new_log = f"{LOG_FILE}.{i + 1}"
                if os.path.exists(old_log):
                    os.rename(old_log, new_log)
            os.rename(LOG_FILE, f"{LOG_FILE}.1")
            open(LOG_FILE, "w").close()


def log(message):
    """Write a message to the log file and print to stdout with a timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} - {message}"
    print(log_entry)  # Print to stdout
    with open(LOG_FILE, "a") as lf:
        lf.write(log_entry + "\n")  # Append to the log file


def compress_source(source):
    """Compress the source directory into a temporary file."""
    compressed_file = f"/tmp/{backup_name}.tar.gz"
    log(f"[INFO] Compressing {source} to {compressed_file}")
    subprocess.run(["tar", "-czvf", compressed_file, *source], check=True, text=True)
    return compressed_file


def rclone_copy(file_path, destination):
    """Copy a file to the destination using rclone."""
    log(f"[INFO] Copying {file_path} to {destination} using rclone")
    subprocess.run(["rclone", "-v", "copy", file_path, destination, "--log-file", LOG_FILE], check=True, text=True)


def rclone_list_files(destination, pattern):
    """List files in the destination directory matching the pattern."""
    result = subprocess.run(["rclone", "lsf", destination, "--order-by", "modtime"], capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if pattern in line]


def rclone_delete(file_path):
    """Delete a file from the destination using rclone."""
    log(f"[INFO] Deleting {file_path}")
    subprocess.run(["rclone", "-v", "delete", file_path, "--log-file", LOG_FILE], check=True, text=True)


def append_to_google_sheet(log_sheet_tab_name, status, spreadsheet_id):
    """Log the backup result to a Google Sheet, including log file content."""

    credentials = service_account.Credentials.from_service_account_file(
        service_account_json_path, 
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )

    sheet_service = build("sheets", "v4", credentials=credentials)

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    with open(LOG_FILE, 'r') as f:
        log_content = f.read()

    try:
        # Check if the tab exists; if not, create it
        sheets_metadata = sheet_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_names = [sheet["properties"]["title"] for sheet in sheets_metadata["sheets"]]

        # Define the range where data will be appended
        range_name = f"{log_sheet_tab_name}!A:D"  # Assuming we have four columns: Timestamp, Status, Google Backup Account, and Log Content

        if log_sheet_tab_name not in sheet_names:
            log(f"[INFO] Tab '{log_sheet_tab_name}' does not exist. Creating it...")
            add_sheet_request = {
                "requests": [
                    {"addSheet": {"properties": {"title": log_sheet_tab_name}}}
                ]
            }
            sheet_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=add_sheet_request).execute()
            column_names = {
                "values": [['Timestamp', 'Status', 'Google Backup Account', 'Log Content']]
            }
            # Add column names to the tab
            sheet_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body=column_names
            ).execute()

        body = {
            "values": [[now, status, google_backup_account, log_content]]
        }
    
        # Append the data to the tab
        sheet_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        log(f"Successfully logged backup status and log content to Google Sheet: {status}")
    except HttpError as error:
        log(f"[ERROR] Failed to update Google Sheet: {error}")
        sys.exit(1)
    
    if status == "FAILURE":
        sys.exit(1)


def get_file_hash(file_path):
    """Calculate the SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_remote_script_content(url):
    """Download the content of the remote script."""        
    # Adding headers to disable caching
    headers = {
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        log(f"[WARNING] Failed to download the remote script: {e}")
        return None


def self_update_script():
    """Compare local script to current and self-update as needed"""

    # Read the current script's content and hash
    CURRENT_SCRIPT_PATH = os.path.abspath(__file__)
    with open(CURRENT_SCRIPT_PATH, "r") as f:
        current_script_content = f.read()
    current_script_hash = hashlib.sha256(current_script_content.encode()).hexdigest()

    # Get the remote script's content and hash
    remote_script_content = get_remote_script_content(REMOTE_SCRIPT_URL)
    if not remote_script_content:
        return
    remote_script_hash = hashlib.sha256(remote_script_content.encode()).hexdigest()

    # Compare hashes to check for differences
    if current_script_hash != remote_script_hash:
        log("[INFO] The script differs from the remote version. Updating...")

        # Update the current script with the remote script
        with open(CURRENT_SCRIPT_PATH, "w") as f:
            f.write(remote_script_content)
        
        # Set permissions to 755
        os.chmod(CURRENT_SCRIPT_PATH, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR |
                             stat.S_IRGRP | stat.S_IXGRP |
                             stat.S_IROTH | stat.S_IXOTH)

        # Set ownership to root:
        subprocess.run(["chown", "root:", CURRENT_SCRIPT_PATH], check=True)

        log("[INFO] Script updated. Re-executing...")
        os.execv(sys.executable, [sys.executable, CURRENT_SCRIPT_PATH] + sys.argv[1:])
        sys.exit(0)

if __name__ == "__main__":

    status = "UNKNOWN"

    rotate_logs()
    log(f'[INFO] Version: {VERSION}')

    parser = argparse.ArgumentParser(description="A script used to backup various config files via rclone.")
    parser.add_argument(
        "--nosleep",
        action="store_true",
        help="Skip the sleep period when executing the script."
    )
    parser.add_argument(
        "--noupdate",
        action="store_true",
        help="Skip the auto-update of script."
    )
    args = parser.parse_args()

    # Check that required software is installed
    check_command("pip")
    check_command("rclone")

    # Attempt to import the required Python non-standard modules and install if import fail
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from google.oauth2 import service_account
    except ImportError as e:
        pip_install("google-api-python-client")
        # check_command("python3-googleapi")
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from google.oauth2 import service_account
    try:
        import requests
    except ImportError as e:
        check_command("python3-requests")
        import requests
    
    if not args.noupdate:
        self_update_script()

    if not args.nosleep:
        # Sleep for a random amount of time between 0 and 1200 seconds (20 minutes)
        # This is to prevent many devices writing to the gsheet log simultaneously 
        #    and bogging down the gsheet auto-formatting
        random_sleep_time = random.uniform(0, 1200)
        log(f"[INFO] Sleeping for {random_sleep_time:.2f} seconds...")
        time.sleep(random_sleep_time)

    # Load configuration from JSON file
    def load_config(config_file="/srv/rclone/config.json"):
        """Load configuration values from a JSON file."""
        if not os.path.exists(config_file):
            log(f"[ERROR] Configuration file '{config_file}' does not exist.")
            sys.exit(1)

        with open(config_file, "r") as f:
            return json.load(f)

    # Configuration loaded from config.json
    config = load_config()
    site = config["site"]
    google_backup_account = config["google_backup_account"]
    backup_types = config["backup_types"]

    # Check that credentials json file exists
    service_account_json_path = "/srv/rclone/rclone-credentials.json"
    if not os.path.exists(service_account_json_path):
        log(f"[ERROR] Configuration file '{service_account_json_path}' does not exist.")
        sys.exit(1)

    # Loop through each backup type
    for backup_type in backup_types:

        spreadsheet_id = None
        exec(f"spreadsheet_id = {backup_type.upper()}_SPREADSHEET_ID")

        destination = f"{RCLONE_REMOTE_NAME}:/{backup_type}/{site}"
        site_backup_name = f"{site}_{backup_type}_backup"
        backup_name = f"{site_backup_name}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        log_sheet_tab_name = f'{backup_type}.{site}'

        # Check if rclone remote exists
        remotes = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True).stdout
        if f"{RCLONE_REMOTE_NAME}:" not in remotes:
            log(f"[ERROR] Rclone remote '{RCLONE_REMOTE_NAME}' does not exist. Exiting.")
            status = "FAILURE"
            append_to_google_sheet(log_sheet_tab_name, status, spreadsheet_id)

        # Check backup source
        source = get_backup_source(backup_type)
        if not source:
            log("[ERROR] No backup source specified. Exiting.")
            status = "FAILURE"
            append_to_google_sheet(log_sheet_tab_name, status, spreadsheet_id)
        elif not type(source) is list:
            log(source)
            status = "FAILURE"
            append_to_google_sheet(log_sheet_tab_name, status, spreadsheet_id)
        else:
            for s in source:
                if os.path.isdir(s):
                    log(f"[INFO] Backup directory found: {s}")
                else:
                    log(f"[ERROR] Backup source '{s}' does not exist. Exiting.")
                    status = "FAILURE"
                    append_to_google_sheet(log_sheet_tab_name, status, spreadsheet_id)
                    sys.exit(1)

        # Compress source directory
        try:
            compressed_file = compress_source(source)
        except Exception as e:
            log(f"[Error] Compressing source failed: {e}")
            status = "FAILURE"
            append_to_google_sheet(log_sheet_tab_name, status, spreadsheet_id)
        else:
            # Copy backup to destination
            try:
                rclone_copy(compressed_file, destination)
                status = "SUCCESS"
            except Exception as e:
                log(f"[ERROR] Copying to destination failed: {e}")
                status = "FAILURE"
                append_to_google_sheet(log_sheet_tab_name, status, spreadsheet_id)
            finally:
                os.remove(compressed_file)

        # Manage backups
        try:
            files_present = rclone_list_files(destination, site_backup_name)
            log("[INFO] Current backups present:")
            for file in files_present:
                log(f"    {file}")

            delete_count = len(files_present) - BACKUP_KEEP_COUNT
            if delete_count > 0:
                log(f"[INFO] More than {BACKUP_KEEP_COUNT} backups present, deleting oldest")
                for file_to_delete in files_present[:delete_count]:
                    rclone_delete(f"{destination}/{file_to_delete}")
        except Exception as e:
            log(f"[ERROR] Managing backups failed: {e}")

        # Log success/failure to Google Sheet
        append_to_google_sheet(log_sheet_tab_name, status, spreadsheet_id)

    sys.exit(0)
