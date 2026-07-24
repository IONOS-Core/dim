from sqlalchemy import Column, BigInteger, String, Text

from dim import db


__all__ = ['SCHEMA_VERSION', 'SchemaInfo']


SCHEMA_VERSION = "13"


class SchemaInfo(db.Model):
    id = Column(BigInteger, primary_key=True, nullable=False)
    version = Column(String(255), nullable=False, unique=True)
    info = Column(Text)

    @staticmethod
    def current_version():
        return SchemaInfo.query.one().version
