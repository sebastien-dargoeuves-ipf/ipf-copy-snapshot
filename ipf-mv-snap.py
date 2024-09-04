import os
import sys
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv
from ipfabric import IPFClient
from ipfabric.models.snapshot import snapshot_upload
from loguru import logger

# Get Current Path
CURRENT_FOLDER = Path(os.path.realpath(os.path.dirname(__file__)))

load_dotenv(find_dotenv(), override=True)

JOB_CHECK_LOOP = 10
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

@app.command()
def main(
    snapshot_src: str = typer.Option("$last", "--snapshot", "-s", help="Snapshot ID to move"),
    server_src: str = typer.Option(os.getenv("IPF_URL_DOWNLOAD"), "--source", "-src", help="IPF Server Source"),
    token_src: str = typer.Option(
        os.getenv("IPF_TOKEN_DOWNLOAD"),
        "--api-source",
        "-api-src",
        help="API Token for Server Source",
    ),
    server_dst: str = typer.Option(
        os.getenv("IPF_URL_UPLOAD"),
        "--destination",
        "-dst",
        help="IPF Server Destination",
    ),
    token_dst: str = typer.Option(
        os.getenv("IPF_TOKEN_UPLOAD"),
        "--api-destination",
        "-api-dst",
        help="Token for Server Destination",
    ),
    keep_dl_file: bool = typer.Option(False, "--keep", "-k", help="Keep the Downloaded Snapshot"),
    timeout_dl: int = typer.Option(5, "--timeout", "-t", help="Timeout to download the Snapshot"),
):
    """
    Move a snapshot from one IPF server to another.

    Args:
        snapshot_src (str): Snapshot ID to move.
        server_src (str): IPF Server Source.
        token_src (str): API Token for Server Source.
        server_dst (str): IPF Server Destination.
        token_dst (str): Token for Server Destination.
        keep_dl_file (bool): Keep the Downloaded Snapshot.
        timeout_dl (int): Timeout to download the Snapshot.

    Returns:
        None

    Raises:
        None

    Examples:
        # Take the last snapshot from the source server, download it and upload it to the destination server
        python ipf-mv-snap.py -src https://ipfabric.source-server -api-src <api-src-token> -dst https://ipfabric.dst-server -api-dst <api-dst-token>

        # Take the specified snapshot from the source server, download it and upload it to the destination server
        python ipf-mv-snap.py -src https://ipfabric.source-server -api-src <api-src-token> -s <snapshot-id> -dst https://ipfabric.dst-server -api-dst <api-dst-token>
    """
    ipf_download = IPFClient(base_url=server_src, auth=token_src, verify=False)
    snapshot = [
        snap
        for snap in ipf_download.snapshots.values()
        if snap.snapshot_id == ipf_download.snapshots[snapshot_src].snapshot_id
    ][0]

    logger.info(f"SOURCE | server: {server_src}, snapshot name: {snapshot.name}, id: {snapshot.snapshot_id}")
    download_path = snapshot.download(retry=JOB_CHECK_LOOP, timeout=timeout_dl)  # retry X timeout = max waiting time
    if not download_path:
        logger.error(
            f"Could not download the file - maybe: job did not finish within {timeout_dl*JOB_CHECK_LOOP} seconds?"
        )
        sys.exit()
    else:
        upload_file = True
    logger.info("download completed, starting upload")

    # Upload the DL snapshot
    try:
        ipf_upload = IPFClient(base_url=server_dst, auth=token_dst, verify=False)
    except Exception as exc:
        upload_file = False
        logger.warning(f"No access to the Upload IPF Server: {exc}")

    if upload_file:
        upload_snap_id = snapshot_upload(ipf_upload, download_path)
        logger.info(
            f"DESTINATION | server: {server_dst}, snapshot name {snapshot.name}, new snap_id: {upload_snap_id}"
        )
    if not keep_dl_file:
        download_path.unlink()
        logger.info(f"File `{download_path}` deleted")


if __name__ == "__main__":
    app()
