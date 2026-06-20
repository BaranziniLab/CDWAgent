"""
CDWAgent - Clinical Data Warehouse MCP Server

An MCP server for querying a de-identified Epic Caboodle Clinical Data Warehouse.
"""

__version__ = "0.5.1"

from cdwagent.config import CDWConfig
from cdwagent.server import create_cdw_server, main

__all__ = ["create_cdw_server", "main", "CDWConfig", "__version__"]
