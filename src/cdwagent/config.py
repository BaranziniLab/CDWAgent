"""CDWAgent configuration models"""

from pydantic import BaseModel, Field


class ClinicalDBConfig(BaseModel):
    """Clinical Data Warehouse database configuration (SQL Server)"""
    server: str = Field(..., description="CDW database server host")
    database: str = Field(..., description="CDW database name")
    username: str = Field(..., description="CDW database username")
    password: str = Field(..., description="CDW database password")


class CDWConfig(BaseModel):
    """Complete CDWAgent server configuration"""
    clinical_db: ClinicalDBConfig = Field(..., description="Clinical Data Warehouse configuration")
    namespace: str = Field("CDW", description="Tool namespace prefix")
    db_schema: str = Field("deid_uf", description="Database schema for table qualification (e.g., deid or deid_uf)")
    log_level: str = Field("INFO", description="Logging level")
