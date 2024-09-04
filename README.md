# IP Fabric Snapshot Mover 🚚

A Python script to copy snapshots between IP Fabric servers.

**Integrations or scripts should not be installed directly on the IP Fabric VM unless directly communicated from the
IP Fabric Support or Solution Architect teams.  Any action on the Command-Line Interface (CLI) using the root, osadmin,
or autoboss account may cause irreversible, detrimental changes to the product and can render the system unusable.**

## Usage

Run the script to move a snapshot from one IPF server to another:

```shell
python copy-snapshot.py [OPTIONS]
```

### Options

- -s, --snapshot: Snapshot ID to move (default: $last).
- -src, --source: IPF Server Source (default from .env).
- -api-src, --api-source: API Token for Server Source (default from .env).
- -dst, --destination: IPF Server Destination (default from .env).
- -api-dst, --api-destination: Token for Server Destination (default from .env).
- -k, --keep: Keep the downloaded snapshot (default: False).
- -t, --timeout: imeout in seconds for each checks during download (default: 5 seconds).

## Environment Variables

The script uses the following environment variables, which should be defined in a `.env` file:

- `IPF_URL_DOWNLOAD`: The URL of the SOURCE IP Fabric instance, where the snapshot will be copied FROM.
- `IPF_TOKEN_DOWNLOAD`: The authentication token for the SOURCE IP Fabric.
- `IPF_URL_UPLOAD`: The URL of the DESTINATION IP Fabric instance, where the snapshot will be copied TO.
- `IPF_TOKEN_UPLOAD`: The authentication token for the DESTINATION IP Fabric.

## Examples

- Copy the last snapshot:

    ```shell
    python copy-snapshot.py
    ```

- Copy a specified snapshot (including unloaded snapshot)

    ```shell
    python copy-snapshot.py -s <snapshot-id>
    ```

- Without the Environment variables specified in the .env file:

    ```shell
    python copy-snapshot.py -src https://ipfabric.source-server -api-src <api-src-token> -dst https://ipfabric.dst-server -api-dst <api-dst-token> -s <snapshot-id>
    ```

## Logging

Logs are saved to logs/ipf-mv-snap.log. The log level can be adjusted in the script.

## License

This project is licensed under the MIT License. See the LICENSE file for details.