import logging.handlers
import os
import sys
from typing import Optional

import flask_sqlalchemy
import sqlalchemy.orm
import sqlalchemy.pool
from flask import Flask, g
from sqlalchemy import event, String
from sqlalchemy.ext.declarative import declared_attr, declarative_base
from sqlalchemy.orm import ColumnProperty

from .errors import InvalidParameterError
from .ipaddr import IP
from . import version

__all__ = ['db', 'create_app', 'script_app']
__version__ = version.VERSION


class TransactionLoggingFormatter(logging.Formatter):
    def format(self, record):
        if g and hasattr(g, 'tid'):
            record.tid = ' ' + g.tid
        else:
            record.tid = ''
        return logging.Formatter.format(self, record)


def create_app(db_mode: Optional[str] = None, testing: bool = False):
    app = Flask(__name__)

    app.config.from_pyfile('defaults.py')
    app.config.from_pyfile(os.getenv('DIM_CONFIG', '/etc/dim/dim.cfg'), silent=True)
    app.config.from_pyfile('../etc/dim.cfg', silent=True)
    os.environ['REQUESTS_CA_BUNDLE'] = app.config['REQUESTS_CA_BUNDLE']
    app.testing = testing
    configure_logging(app)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    if db_mode:
        app.config['SQLALCHEMY_DATABASE_URI'] = app.config[
            'SQLALCHEMY_DATABASE_URI_' + db_mode
        ]

    if testing:
        app.config['LAYER3DOMAIN_WHITELIST'] = app.config.get(
            'LAYER3DOMAIN_WHITELIST', ['10.0.0.0/8', '176.16.0.0/12', '192.168.0.0/16']
        )

    if 'LAYER3DOMAIN_WHITELIST' not in app.config:
        logging.error('LAYER3DOMAIN_WHITELIST not set')
        sys.exit(1)
    try:
        app.config['LAYER3DOMAIN_WHITELIST'] = [
            IP(ip_str) for ip_str in app.config['LAYER3DOMAIN_WHITELIST']
        ]
    except:
        logging.error('Error parsing LAYER3DOMAIN_WHITELIST', exc_info=True)
        sys.exit(1)
    db.init_app(app)

    # Check if sql scheme is what the code expects to not run into wierd errors
    if not testing:
        from .models import SCHEMA_VERSION, SchemaInfo

        # TODO: find a good way to make sure that this is only set on db commands
        is_cli_db_command = "manage_db" in sys.argv[0] or "flask" in sys.argv[0]
        try:
            with app.app_context():
                current_db_schema_version = SchemaInfo.current_version()
                if current_db_schema_version != SCHEMA_VERSION:
                    logging.critical(
                        "DATABASE SCHEMA MISMATCH: Database version is %s, but code expects %s. "
                        "Please run 'manage_db upgrade' before starting the application!",
                        current_db_schema_version,
                        SCHEMA_VERSION,
                    )
                    if not is_cli_db_command:
                        sys.exit(
                            1
                        )  # Do we really want to exit here? My fear is that we never can run manage_db *
        except:
            logging.info(
                "Could not verify database schema version on startup, database might be empty."
                "We let the app running to allow 'manage_db *'"
            )

    from .jsonrpc import jsonrpc

    app.register_blueprint(jsonrpc)
    # add manage_dim commands
    from .manage_dim import manage_dim
    app.register_blueprint(manage_dim)
    # add manage_db commands
    from .manage_db import manage_db
    app.register_blueprint(manage_db)
    # add report commands
    from .report import dim_report
    app.register_blueprint(dim_report)
    return app


def script_app(*args, **kwargs):
    app = create_app(*args, **kwargs)
    app.test_request_context().push()
    return app


def configure_logging(app):
    if app.testing:
        logging.basicConfig(
            level=app.config['LOGGING_LEVEL'], format='%(levelname)-5s:%(message)s'
        )
        if app.config['SQLALCHEMY_LOG']:
            logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
        return
    root_logger = logging.getLogger()
    root_logger.setLevel(app.config['LOGGING_LEVEL'])
    for h in root_logger.handlers:
        root_logger.removeHandler(h)

    formatter = TransactionLoggingFormatter('%(levelname)-5s%(tid)s - %(message)s')
    handler = app.config.get('LOGGING_HANDLER', logging.StreamHandler())
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    if app.config['SQLALCHEMY_LOG']:
        logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)


class Model(object):
    query_class = sqlalchemy.orm.Query
    default_table_kwargs = {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8'}

    def __repr__(self):
        def reprs():
            for name in list(self.__mapper__.c.keys()):
                yield name, repr(getattr(self, name))

        def format(seq):
            for key, value in seq:
                yield '%s=%s' % (key, value)

        args = '(%s)' % ', '.join(format(reprs()))
        classy = type(self).__name__
        return classy + args

    @declared_attr
    def __table_args__(cls):
        return getattr(cls, '__table_constraints__', ()) + (Model.default_table_kwargs,)

    @declared_attr
    def __tablename__(cls):
        return cls.__name__.lower()


db = flask_sqlalchemy.SQLAlchemy()
db.Model = declarative_base(cls=Model)
db.Model.query = flask_sqlalchemy._QueryProperty(db)


# set timezone for mysql
@event.listens_for(sqlalchemy.pool.Pool, "connect")
def set_timezone(dbapi_con, con_record):
    cursor = dbapi_con.cursor()
    cursor.execute("SET time_zone = '+0:00'")
    cursor.close()


# We need to check the string lengths because MySQL silently truncates string
# values which are too long
def check_string_length(cls, key, inst):
    prop = inst.prop
    # Only interested in simple columns, not relations
    if isinstance(prop, ColumnProperty) and len(prop.columns) == 1:
        col = prop.columns[0]
        # if we have string column with a length, install a length validator
        if isinstance(col.type, String) and col.type.length:
            max_length = col.type.length

            def set_(instance, value, oldvalue, initiator):
                if isinstance(value, str) and len(value) > max_length:
                    raise InvalidParameterError(
                        "Field %s exceeds maximum length %d" % (col.name, max_length)
                    )

            event.listen(inst, 'set', set_)


event.listen(db.Model, 'attribute_instrument', check_string_length)
