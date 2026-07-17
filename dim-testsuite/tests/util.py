import unittest
import logging
from contextlib import contextmanager
from pprint import saferepr as safe_repr
from dim import create_app, db, rpc
from dim.ipaddr import IP
from dim.models import clean_database, User, Group, AccessRight, Layer3Domain, Ipblock


@contextmanager
def raises(error, message=None):
    try:
        yield
    except error as e:
        if message is not None:
            err_msg = str(e)
            if message not in err_msg:
                raise AssertionError("Expected exception message containing '%s' but got '%s'" % (message, err_msg))
    except Exception as e:
        logging.exception(e)
        raise AssertionError("Expected exception %s but got %s" % (error.__name__, e.__class__.__name__))
    else:
        raise AssertionError("No exception raised")


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app('TEST', testing=True)
        self.ctx = self.app.test_request_context()
        self.ctx.push()
        clean_database()

    def tearDown(self):
        db.session.remove()
        db.get_engine(self.app).dispose()
        self.ctx.pop()

    # taken from unittest/python 2.7
    def assertDictSubset(self, actual, expected, msg=None):
        """Checks whether actual is a superset of expected."""
        missing = []
        mismatched = []
        for key, value in expected.items():
            if key not in actual:
                missing.append(key)
            elif value != actual[key]:
                mismatched.append('%s, expected: %s, actual: %s' %
                                  (safe_repr(key), safe_repr(value),
                                   safe_repr(actual[key])))

        if not (missing or mismatched):
            return

        msg = ''
        if missing:
            msg += 'Missing: %s' % ','.join(safe_repr(m) for m in missing)
        if mismatched:
            if len(msg) > 0:
                msg += '; '
            msg += 'Mismatched values: %s' % ','.join(mismatched)
        self.fail(msg)


class RPCTest(DatabaseTest):
    def setUp(self):
        DatabaseTest.setUp(self)
        group = Group(name='group')
        group.users.add(User('test_user'))
        group.rights.add(AccessRight(access='network_admin', object_class='all', object_id=0))
        group.rights.add(AccessRight(access='dns_admin', object_class='all', object_id=0))
        db.session.add(group)
        db.session.commit()
        self.r = rpc.TRPC('test_user')


def query_ip(ip_str):
    layer3domain = Layer3Domain.query.first()
    return Ipblock.query_ip(IP(ip_str), layer3domain)
