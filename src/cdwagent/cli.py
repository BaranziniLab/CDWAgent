"""CDWAgent CLI entry point"""

import logging
import os

from cdwagent.server import main as server_main

logger = logging.getLogger("CDWAgent")


def main() -> None:
    """CLI entry point — reads env vars and starts the server."""
    log_level = os.getenv("CDW_LOG_LEVEL", "INFO")
    logging.basicConfig(level=getattr(logging, log_level.upper()))

    logger.info("Starting CDWAgent - Clinical Data Warehouse MCP Server")

    server_main(
        clinical_records_server=os.getenv("CLINICAL_RECORDS_SERVER"),
        clinical_records_database=os.getenv("CLINICAL_RECORDS_DATABASE"),
        clinical_records_username=os.getenv("CLINICAL_RECORDS_USERNAME"),
        clinical_records_password=os.getenv("CLINICAL_RECORDS_PASSWORD"),
        namespace=os.getenv("CDW_NAMESPACE", "CDW"),
        schema=os.getenv("CDW_SCHEMA", "deid_uf"),
        log_level=log_level,
    )


if __name__ == "__main__":
    main()
