import ast
import inspect
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx
import typer
from dotenv import find_dotenv, load_dotenv
from ipfabric import IPFClient
from loguru import logger
from rich.console import Console
from rich.table import Table

load_dotenv(find_dotenv(), override=True)

# Get Current Path
CURRENT_FOLDER = Path(os.path.realpath(os.path.dirname(__file__)))

JOB_CHECK_LOOP = 30
DEFAULT_TIMEOUT = 5
LOG_FILE = CURRENT_FOLDER / "logs/ipf-mv-snap.log"
HTTP_400_STATUS = 400

logger.remove()
logger.add(
    sys.stderr,
    colorize=True,
    level="INFO",
    # format=LOGGER_FORMAT,
    diagnose=False,
)
logger.add(
    LOG_FILE,
    colorize=True,
    level="DEBUG",
    # format=LOGGER_FORMAT,
    diagnose=False,
    rotation="1 MB",
    compression="bz2",
    retention="2 months",
)
logger.info("-------------- STARTING SCRIPT --------------")

class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding Loguru level if it exists.
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message.
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

app = typer.Typer(add_completion=False)
console = Console()


def parse_selection(selection: str, max_index: int) -> list[int]:
    """
    Parse a selection string like '1,3,5' or '1-3,5,7-9' into a list of indices.
    Returns 0-based indices.
    """
    indices = set()
    parts = selection.replace(" ", "").split(",")
    for part in parts:
        if "-" in part:
            match = re.match(r"(\d+)-(\d+)", part)
            if match:
                start, end = int(match.group(1)), int(match.group(2))
                for i in range(start, end + 1):
                    if 1 <= i <= max_index:
                        indices.add(i - 1)  # Convert to 0-based
        elif part.isdigit():
            idx = int(part)
            if 1 <= idx <= max_index:
                indices.add(idx - 1)  # Convert to 0-based
    return sorted(indices)


def display_snapshots(snapshots: list) -> None:
    """Display snapshots in a formatted table."""
    table = Table(title="Available Snapshots")
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Name", style="green")
    table.add_column("Date", style="yellow")
    table.add_column("Snapshot ID", style="magenta")

    for idx, snap in enumerate(snapshots, 1):
        # Try to get the date - could be start, end, or creation timestamp
        snap_date = ""
        if hasattr(snap, "start") and snap.start:
            # Handle both datetime objects and timestamps
            if isinstance(snap.start, datetime):
                snap_date = snap.start.strftime("%Y-%m-%d %H:%M")
            else:
                snap_date = datetime.fromtimestamp(snap.start / 1000).strftime("%Y-%m-%d %H:%M")
        elif hasattr(snap, "end") and snap.end:
            if isinstance(snap.end, datetime):
                snap_date = snap.end.strftime("%Y-%m-%d %H:%M")
            else:
                snap_date = datetime.fromtimestamp(snap.end / 1000).strftime("%Y-%m-%d %H:%M")

        table.add_row(str(idx), snap.name or "N/A", snap_date or "N/A", snap.snapshot_id)

    console.print(table)


def copy_single_snapshot(
    snapshot,
    server_src: str,
    server_dst: str,
    auth_dst: str,
    keep_dl_file: bool,
    dl_check_timeout: int,
) -> tuple[bool, str]:
    """
    Copy a single snapshot from source to destination.
    Returns (success: bool, error_message: str).
    """
    try:
        logger.info(f"SOURCE | server: {server_src}, snapshot name: {snapshot.name}, id: {snapshot.snapshot_id}")
        logger.info("🔄 Downloading in progress...")
        download_path = snapshot.download(retry=JOB_CHECK_LOOP, timeout=dl_check_timeout)
        logger.info(f"✅ Download completed | file: {download_path.absolute()}")

        # Upload the snapshot
        try:
            logger.info(f"Initiating upload to {server_dst}...")
            upload_snap_id = snap_upload(server_dst=server_dst, filename=download_path, api_token=parse_auth(auth_dst))
            logger.info("✅ Upload snapshot to the server completed.")
            logger.info(
                f"DESTINATION | server: {server_dst}, snapshot name {snapshot.name}, new snap_id: {upload_snap_id}"
            )
        except Exception as exc:
            logger.error(f"Could not upload the file: {exc}")
            if not keep_dl_file:
                download_path.unlink()
                logger.warning("Deleting the file, as `keep_dl_file` is False.")
            return False, f"Upload failed: {exc}"

        if not keep_dl_file:
            download_path.unlink()
            logger.info(f"File `{download_path.absolute()}` deleted")

        return True, ""

    except Exception as exc:
        logger.error(f"Error processing snapshot {snapshot.snapshot_id}: {exc}")
        return False, str(exc)


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
    """Upload a snapshot to an IPF server."""
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
    if resp.status_code == HTTP_400_STATUS and resp.json().get("code") == "API_SNAPSHOT_CONFLICT":
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
        os.getenv("IPF_AUTH_DOWNLOAD"),
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
        os.getenv("IPF_AUTH_UPLOAD"),
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
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Interactive mode to select multiple snapshots"),
):
    """Copy a snapshot from one IPF server to another.

    Args:
    ----
        snapshot_src (str): Snapshot ID to copy.
        server_src (str): IPF Server Source.
        auth_src (str): API Token for Server Source, or "('user', 'password')".
        server_dst (str): IPF Server Destination.
        auth_dst (str): Token for Server Destination, or "('user', 'password')".
        keep_dl_file (bool): Keep the Downloaded Snapshot.
        dl_check_timeout (int): Timeout to download the Snapshot.
        interactive (bool): Interactive mode to select multiple snapshots.

    Returns:
    -------
        None

    Raises:
    ------
        None

    """
    ipf_download = IPFClient(base_url=server_src, auth=parse_auth(auth_src), verify=False, unloaded=True)

    # Interactive mode: display snapshots and let user select
    if interactive:
        # Get all snapshots and sort by date (newest first)
        all_snapshots = list(ipf_download.snapshots.values())
        all_snapshots.sort(key=lambda s: s.start if hasattr(s, "start") and s.start else datetime.min, reverse=True)

        if not all_snapshots:
            logger.error("No snapshots found on the source server.")
            raise typer.Exit(1)

        console.print(f"\n[bold]Source server:[/bold] {server_src}\n")
        display_snapshots(all_snapshots)

        # Prompt for selection
        console.print("\n[bold]Enter snapshot numbers to copy[/bold] (e.g., 1,3,5 or 1-3,5-7):")
        selection = typer.prompt("Selection")

        selected_indices = parse_selection(selection, len(all_snapshots))

        if not selected_indices:
            logger.error("No valid snapshots selected.")
            raise typer.Exit(1)

        selected_snapshots = [all_snapshots[i] for i in selected_indices]

        # Confirmation
        console.print("\n[bold yellow]You selected the following snapshots:[/bold yellow]")
        for snap in selected_snapshots:
            console.print(f"  • {snap.name} ({snap.snapshot_id})")

        console.print(f"\n[bold]Destination server:[/bold] {server_dst}")
        if not typer.confirm("\nProceed with copying these snapshots?"):
            logger.info("Operation cancelled by user.")
            raise typer.Exit(0)

        # Process each snapshot
        results = {"success": [], "failed": []}

        for idx, snapshot in enumerate(selected_snapshots, 1):
            console.print(f"\n[bold cyan]Processing snapshot {idx}/{len(selected_snapshots)}:[/bold cyan] {snapshot.name}")
            success, error = copy_single_snapshot(
                snapshot=snapshot,
                server_src=server_src,
                server_dst=server_dst,
                auth_dst=auth_dst,
                keep_dl_file=keep_dl_file,
                dl_check_timeout=dl_check_timeout,
            )
            if success:
                results["success"].append(snapshot)
            else:
                results["failed"].append((snapshot, error))

        # Summary report
        console.print("\n" + "=" * 60)
        console.print("[bold]COPY SUMMARY[/bold]")
        console.print("=" * 60)

        if results["success"]:
            console.print(f"\n[bold green]✅ Successfully copied ({len(results['success'])}):[/bold green]")
            for snap in results["success"]:
                console.print(f"   • {snap.name} ({snap.snapshot_id})")

        if results["failed"]:
            console.print(f"\n[bold red]❌ Failed ({len(results['failed'])}):[/bold red]")
            for snap, error in results["failed"]:
                console.print(f"   • {snap.name} ({snap.snapshot_id}): {error}")

        console.print("\n" + "=" * 60)
        logger.info("⏳ Loading in progress... script is done, but snapshots may still be loading.")
        return

    # Non-interactive mode: original behavior
    upload_file = True
    snapshot = [
        snap
        for snap in ipf_download.snapshots.values()
        if snap.snapshot_id == ipf_download.snapshots[snapshot_src].snapshot_id
    ][0]

    logger.info(f"SOURCE | server: {server_src}, snapshot name: {snapshot.name}, id: {snapshot.snapshot_id}")
    logger.info("🔄 Downloading in progress...")
    download_path = snapshot.download(retry=JOB_CHECK_LOOP, timeout=dl_check_timeout)
    # if not download_path:
    #     logger.error("Could not download the file - let's try again")
    #     time.sleep(dl_check_timeout)
    #     download_path = snapshot.download(retry=JOB_CHECK_LOOP, timeout=dl_check_timeout)
    #     if not download_path:
    #         logger.error(
    #             f"""Could not download the file again...
    #             maybe the job did not finish within {dl_check_timeout*JOB_CHECK_LOOP} seconds?"""
    #         )
    #     sys.exit()
    logger.info(f"✅ Download completed | file: {download_path.absolute()}")

    # Upload the DL snapshot without using the IPFClient, so we can use different versions of the API
    if upload_file:
        try:
            logger.info(f"Initiating upload to {server_dst}...")
            upload_snap_id = snap_upload(server_dst=server_dst, filename=download_path, api_token=parse_auth(auth_dst))
            logger.info("✅ Upload snapshot to the server completed.")
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
        logger.info(f"File `{download_path.absolute()}` deleted")

    if upload_file:
        logger.info("⏳ Loading in progress... script is done, but the snapshot is still loading.")


if __name__ == "__main__":
    app()
