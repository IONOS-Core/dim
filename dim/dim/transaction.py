import logging
import time
import uuid
from functools import wraps

from flask import current_app, g
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from dim import db
from dim.messages import Messages
from dim.models import get_session_username
from dim.util import safe_repr


def user_session(username, tool, ip):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            g.username = username
            g.tool = tool
            g.ip_address = getattr(ip, 'address', None)
            g.ip_version = getattr(ip, 'version', None)
            try:
                return f(*args, **kwargs)
            finally:
                del g.username
        return wrapper
    return decorator


def time_function(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        start = time.time()
        name = "%s(%s)" % (f.__name__, ', '.join([safe_repr(a) for a in args] +
                ["%s=%s" % (k, safe_repr(v)) for k, v in list(kwargs.items())]))
        try:
            Messages.clear()
            g.tid = uuid.uuid4().hex[16:]
            logging.info("%s called %s", get_session_username(), name)
            return f(*args, **kwargs)
        finally:
            logging.info("%.3lf for %s", time.time() - start, name)
    return wrapper


class LockTimeout(Exception):
    pass


def get_lock_for_transaction(lock_name, timeout):
    if not db.session.execute("SELECT GET_LOCK('%s', %d)" % (lock_name, timeout)).scalar():
        raise LockTimeout("Lock timeout")

    # RELEASE_LOCK must be called from the same connection.
    # after_commit/after_rollback hooks must be used because
    # commit()/rollback() will release the connection.  Even if the
    # connection is not released yet, the session might be inactive when the
    # hooks are called, so we cannot use session.execute().
    conn = db.session.connection()

    def release_lock(*args):
        if not conn.closed:
            released = conn.execute("SELECT RELEASE_LOCK('%s')" % lock_name).scalar()
            assert released == 1
    event.listen(db.session(), 'after_commit', release_lock)
    event.listen(db.session(), 'after_rollback', release_lock)


def retryable_transaction(f):
    """Retry a function when deadlocky exceptions are raised"""
    attempts = 3
    lock_messages_error = ['Deadlock found', 'Lock wait timeout exceeded']

    @wraps(f)
    def wrapped(*args, **kwargs):
        Messages.save()
        for i in range(attempts):
            if i > 0:
                Messages.restore()
            try:
                return f(*args, **kwargs)
            except OperationalError as e:
                if i == (attempts - 1) or all(msg not in e.message for msg in lock_messages_error):
                    raise
                logging.info('Error processing transaction: %s. Retrying', e)
    return wrapped


def _transaction(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        dryrun = kwargs.pop('dryrun', False)
        if not dryrun:
            get_lock_for_transaction('netdot_transaction', current_app.config['DB_LOCK_TIMEOUT'])
        try:
            result = f(*args, **kwargs)
            if dryrun:
                db.session.rollback()
            else:
                db.session.commit()
            return result
        except:
            db.session.rollback()
            raise
    return wrapper


def _transaction_delayed_lock(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        dryrun = kwargs.pop('dryrun', False)

        def rollback_and_get_lock():
            if not dryrun:
                db.session.rollback()
                get_lock_for_transaction('netdot_transaction', current_app.config['DB_LOCK_TIMEOUT'])
        try:
            g.rollback_and_get_lock = rollback_and_get_lock
            result = f(*args, **kwargs)
            if dryrun:
                logging.info("Rollback due to dryrun")
                db.session.rollback()
            else:
                db.session.commit()
            return result
        except:
            db.session.rollback()
            raise
    return wrapper


def transaction(f):
    return retryable_transaction(_transaction(f))


def transaction_delayed_lock(f):
    return retryable_transaction(_transaction_delayed_lock(f))


def _ignore_dryrun(f):
    '''A read-only call has nothing to roll back, so dryrun does not apply to it.

    ndcli -n sends dryrun with every call, read-only ones included.'''
    @wraps(f)
    def wrapper(*args, **kwargs):
        kwargs.pop('dryrun', None)
        return f(*args, **kwargs)
    return wrapper


class TransactionProxy(object):
    def __init__(self, obj):
        self.obj = obj

    def __getattr__(self, name):
        if not name.startswith('_'):
            func = getattr(self.obj, name)
            if callable(func):
                if getattr(func, 'readonly', False):
                    return user_session(self.obj.username, self.obj.tool, self.obj.ip)(time_function(_ignore_dryrun(func)))
                elif getattr(func, 'delayed_lock', False):
                    return user_session(self.obj.username, self.obj.tool, self.obj.ip)(
                        time_function(transaction_delayed_lock(func)))
                else:
                    return user_session(self.obj.username, self.obj.tool, self.obj.ip)(time_function(transaction(func)))
        raise AttributeError
