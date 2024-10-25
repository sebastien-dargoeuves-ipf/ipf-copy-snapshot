import ast
import os
from pathlib import Path

import typer
import sys
import httpx
from dotenv import find_dotenv, load_dotenv
from ipfabric import IPFClient
from loguru import logger

# Get Current Path
CURRENT_FOLDER = Path(os.path.realpath(os.path.dirname(__file__)))

load_dotenv(find_dotenv(), override=True)

JOB_CHECK_LOOP = 60
DEFAULT_TIMEOUT = 5
LOG_FILE = CURRENT_FOLDER / "logs/ipf-mv-snap.log"
LOGGER_FORMAT = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <yellow><italic>{elapsed}</italic></yellow> | <level>{level: <8}</level> | <level>{message}</level>"

logger.remove()
logger.add(
    sys.stderr,
    colorize=True,
    level="INFO",
    format=LOGGER_FORMAT,
    diagnose=False,
)
logger.add(
    LOG_FILE,
    colorize=True,
    level="DEBUG",
    format=LOGGER_FORMAT,
    diagnose=False,
    rotation="1 MB",
    compression="bz2",
    retention="2 months",
)
logger.info("-------------- STARTING SCRIPT --------------")

app = typer.Typer(add_completion=False)


def parse_auth(auth):
    """
    Parse the auth string to a tuple if it resembles one, otherwise return the string.
    We use this function to handle the different ways to pass the auth credentials.
    """
    if not isinstance(auth, str):
        # If it's not a string, raise an error or handle accordingly
        raise TypeError("auth must be a string.")
        # Attempt to convert the string to a tuple
    try:
        # Check if the string resembles a tuple
        return ast.literal_eval(auth) if auth.startswith("(") and auth.endswith(")") else auth
    except (ValueError, SyntaxError):
        # If conversion fails, treat it as an API token
        return auth


def snap_upload(server_dst: str, filename: str, api_token: str):
    """
    Upload a snapshot to an IPF server.
    """
    # Prepare the file to be uploaded
    file_data = {"file": (Path(filename).name, open(filename, "rb"), "application/x-tar")}
    headers = {"x-api-token": api_token}
    # Get the API version
    get_api_version = httpx.get(f"{server_dst}/api/version", verify=False)
    get_api_version.raise_for_status()
    api_version = get_api_version.json().get("apiVersion")
    # Make the POST request to upload the file
    resp = httpx.post(
        f"{server_dst}/api/{api_version}/snapshots/upload", files=file_data, headers=headers, verify=False
    )

    # Check for a 400 response and handle specific error codes
    if resp.status_code == 400 and resp.json().get("code") == "API_SNAPSHOT_CONFLICT":
        snapshot_id = resp.json().get("data", {}).get("snapshot")
        print(f"SNAPSHOT ID {snapshot_id} already uploaded")
        return

    # Raise an exception for other error statuses
    resp.raise_for_status()
    return resp.json()


@app.command()
def main(
    snapshot_src: str = typer.Option("$last", "--snapshot", "-s", help="Snapshot ID to copy"),
    server_src: str = typer.Option(os.getenv("IPF_URL_DOWNLOAD"), "--source", "-src", help="IPF Server Source"),
    auth_src: str = typer.Option(
        os.getenv("IPF_AUTH_DOWNLOAD", ("admin", os.getenv("IPF_USER_PWD"))),
        "--auth-source",
        "-auth-src",
        help="API Token for Server Source, or use \"('user', 'password')\"",
    ),
    server_dst: str = typer.Option(
        os.getenv("IPF_URL_UPLOAD", os.getenv("IPF_URL_TS")),
        "--destination",
        "-dst",
        help="IPF Server Destination",
    ),
    auth_dst: str = typer.Option(
        os.getenv("IPF_AUTH_UPLOAD", os.getenv("IPF_TOKEN_TS")),
        "--auth-destination",
        "-auth-dst",
        help="Token for Server Destination, or use \"('user', 'password')\"",
    ),
    keep_dl_file: bool = typer.Option(False, "--keep", "-k", help="Keep the Downloaded Snapshot"),
    dl_check_timeout: int = typer.Option(
        DEFAULT_TIMEOUT,
        "--timeout",
        "-t",
        help="Timeout in seconds for each checks during download",
    ),
):
    """
    Copy a snapshot from one IPF server to another.

    Args:
        snapshot_src (str): Snapshot ID to copy.
        server_src (str): IPF Server Source.
        auth_src (str): API Token for Server Source, or "('user', 'password')".
        server_dst (str): IPF Server Destination.
        auth_dst (str): Token for Server Destination, or "('user', 'password')".
        keep_dl_file (bool): Keep the Downloaded Snapshot.
        dl_check_timeout (int): Timeout to download the Snapshot.

    Returns:
        None

    Raises:
        None

    """
    upload_file = True
    ipf_download = IPFClient(base_url=server_src, auth=parse_auth(auth_src), verify=False, unloaded=True)
    snapshot = [
        snap
        for snap in ipf_download.snapshots.values()
        if snap.snapshot_id == ipf_download.snapshots[snapshot_src].snapshot_id
    ][0]

    logger.info(f"SOURCE | server: {server_src}, snapshot name: {snapshot.name}, id: {snapshot.snapshot_id}")
    download_path = snapshot.download(
        retry=JOB_CHECK_LOOP, timeout=dl_check_timeout
    )  # retry X timeout = max waiting time
    if not download_path:
        logger.error(
            f"Could not download the file - maybe: job did not finish within {dl_check_timeout*JOB_CHECK_LOOP} seconds?"
        )
        sys.exit()
    logger.info(f"✅ Download completed | file: {download_path.absolute()}")

    # Upload the DL snapshot without using the IPFClient, so we can use different versions of the API
    if upload_file:
        try:
            logger.info(f"Initiating upload to {server_dst}...")
            upload_snap_id = snap_upload(server_dst=server_dst, filename=download_path, api_token=parse_auth(auth_dst))
            logger.info("✅ Upload initiated")
            logger.info(
                f"DESTINATION | server: {server_dst}, snapshot name {snapshot.name}, new snap_id: {upload_snap_id}"
            )
        except Exception as exc:
            logger.error(f"Could not upload the file: {exc}")
            if not keep_dl_file:
                download_path.unlink()
                logger.warning("Deleting the file, as `keep_dl_file` is False.")
                sys.exit()
    if not keep_dl_file:
        download_path.unlink()
        logger.info(f"File `{download_path}` deleted")

    if upload_file:
        logger.info("⏳ Loading snapshot... script is done, but the snapshot is still loading.")


if __name__ == "__main__":
    app()
