
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
import dotenv
import typer
from ipfabric import IPFClient
from ipfabric.models.snapshot import snapshot_upload
from loguru import logger

# Get Current Path
CURRENT_PATH = Path(os.path.realpath(os.path.dirname(sys.argv[0]))).resolve()

dotenv.load_dotenv(dotenv.find_dotenv())

LOG_FILE = CURRENT_PATH / "logs/loguru_ts_analysis.log"
LOGGER_DEBUG_FORMAT = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <yellow><italic>{elapsed}</italic></yellow> | <level>{level: <8}</level> | <level>{message}</level>"
LOGGER_INFO_FORMAT = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <yellow><italic>{elapsed}</italic></yellow> | <level>{message}</level>"

logger.remove()
logger.add(
    sys.stderr,
    colorize=True,
    level="INFO",
    format=LOGGER_INFO_FORMAT,
    diagnose=False,
)
logger.add(
    LOG_FILE,
    colorize=True,
    level="INFO",
    format=LOGGER_DEBUG_FORMAT,
    diagnose=False,
    rotation="1 MB",
    compression="bz2",
    retention="2 months",
)
logger.info("-------------- STARTING SCRIPT --------------")

app = typer.Typer(add_completion=False)

# @logger.catch
@app.command()
def main(
    snapshot_src: str = typer.Option(
        "$last", "--snapshot", "-s", help="Snapshot ID to move"
    ),
    server_src: str = typer.Option(
        os.getenv("IPF_URL_DOWNLOAD"), "--source", "-src", help="IPF Server Source"
    ),
    token_src: str = typer.Option(
        os.getenv("IPF_TOKEN_DOWNLOAD"), "--api-source", "-api-src", help="API Token for Server Source"
    ),
    server_dst: str = typer.Option(
        os.getenv("IPF_URL_UPLOAD"), "--destination", "-dst", help="IPF Server Destination"
    ),
    token_dst: str = typer.Option(
        os.getenv("IPF_TOKEN_UPLOAD"), "--api-destination", "-api-dst", help="Token for Server Destination"
    ),
    keep_dl_file: bool = typer.Option(False, "--keep", "-k", help="Keep the Downloaded Snapshot"),
    timeout_dl: int = typer.Option(5, "--timeout", "-t", help="Timeout to download the Snapshot")
):

    ipf_download = IPFClient(base_url=server_src, auth=token_src, verify=False)
    snapshot = [snap for snap in ipf_download.snapshots.values() if snap.snapshot_id == ipf_download.snapshots[snapshot_src].snapshot_id][0]

    logger.info(f"name: {snapshot.name}, id: {snapshot.snapshot_id}")
    download_path = snapshot.download(ipf_download, retry=5, timeout=timeout_dl)  # 5 x 5 = 25 seconds
    if not download_path:
        sys.exit("ERR - Issue while downloading the file")
    else:
        upload_file = True
    logger.info("download completed, starting upload")

    try:
        ipf_upload = IPFClient(base_url=server_dst, auth=token_dst, verify=False)
    except Exception as exc:
        upload_file = False
        logger.warning(f"No access to the Upload IPF Server: {exc}")

    if upload_file:
        upload_snap_id = snapshot_upload(ipf_upload, download_path)
        logger.info(
            f"uploaded snapshot {snapshot.name} to {os.getenv('IPF_URL_UPLOAD')} new snap_id = {upload_snap_id}"
        )
    if not keep_dl_file:
        download_path.unlink()
        logger.info(f"File `{download_path}` deleted")
    

if __name__ == '__main__':
    app()
