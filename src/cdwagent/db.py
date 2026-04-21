"""Database connection management (identical pattern to MedCP)"""

import logging

import pymssql
from fastmcp.exceptions import ToolError

from cdwagent.config import ClinicalDBConfig

logger = logging.getLogger("CDWAgent")


def get_connection(config: ClinicalDBConfig):
    """Get a per-query database connection"""
    try:
        return pymssql.connect(
            server=config.server,
            user=config.username,
            password=config.password,
            database=config.database
        )
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise ToolError(f"Database connection failed: {e}")
