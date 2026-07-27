

import logging
import sys

import ldap3
import ssl
from flask import current_app as app

from dim import db
from dim.models import Group, User, GroupMembership, Department
from dim.transaction import time_function, transaction
from typing import List, Dict

from sqlalchemy.orm.exc import NoResultFound


class LDAP(object):
    def __init__(self):
        server_kwargs = {}
        tls_kwargs = app.config.get_namespace('LDAP_SERVER_TLS_')
        if app.config['LDAP_SERVER'].startswith('ldaps'):
            if 'validate' in tls_kwargs.keys() and not tls_kwargs['validate']:
                tls_kwargs['validate'] = ssl.CERT_NONE
            else:
                tls_kwargs['validate'] = ssl.CERT_REQUIRED
            server_kwargs['tls'] = ldap3.Tls(**tls_kwargs)

        ldap_server = ldap3.Server(app.config['LDAP_SERVER'], **server_kwargs)
        conn = ldap3.Connection(ldap_server, read_only=True, client_strategy=ldap3.SAFE_SYNC)
        try:
            (status, result, response, request) = conn.bind()
        except ldap3.core.exceptions.LDAPExceptionError as e:
            logging.exception('Error connecting to ldap server %s: %s', ldap_server, e)
            raise
        if not status:
            logging.exception('Error connecting to ldap server %s: %s', ldap_server, result)
            raise
        self.conn = conn

    def query(self, base: str, search_filter: str, attributes: List[str] = None):
        try:
            status, result, response, _ = self.conn.search(base, search_filter, attributes=attributes, search_scope=ldap3.LEVEL)
            return response
        except:
            logging.exception('Error in LDAP query %s %s', base, search_filter)
            raise

    def users(
        self,
        search_filter: str,
        base_dn: str = None,
        attributes: List[str] = ["o", "cn", "uid", "departmentNumber", "personid"],
    ) -> List[User]:
        """
        Queries the LDAP server for users matching a specific filter and returns a list of User objects.
        This method is resilient against missing attributes and empty lists to prevent index errors.

        :param search_filter: The LDAP search filter (e.g., '(o=username)')
        :param base_dn: Optional. The starting point in the LDAP directory. Defaults to LDAP_USER_BASE.
        :param attributes: The list of LDAP attributes to fetch.
        :return: A list of User objects with populated attributes.
        """
        # Resolve the default user base if none was provided (to maintain backward compatibility)
        if base_dn is None:
            base_dn = app.config["LDAP_USER_BASE"]

        # Safe converter: Converts a value to an integer, returning None if empty or invalid
        def fix_int(s):
            return int(s) if s is not None else s

        # Robust pseudo converter for department number
        def fix_pseudo(s):
            """
            Some LDAP accounts (like pseudo users) might not have a department_number.
            We don't want the program to fail, so we catch ValueError.
            """
            try:
                if s is not None:
                    return int(s)
                else:
                    return s
            except ValueError:
                return None

        # Safe attribute reader: Avoids IndexError by checking if the list has elements before reading index 0
        def get_first_value(attributes_dict, attribute_name):
            lst = attributes_dict.get(attribute_name)
            return lst[0] if lst else None

        return [
            User(
                username=get_first_value(u["attributes"], "o")
                or get_first_value(u["attributes"], "uid"),
                ldap_cn=get_first_value(u["attributes"], "cn"),
                ldap_uid=fix_int(get_first_value(u["attributes"], "uid"))
                or fix_int(get_first_value(u["attributes"], "personid")),
                department_number=fix_pseudo(
                    get_first_value(u["attributes"], "departmentNumber")
                ),
                register=False,
            )
            for u in self.query(base_dn, search_filter, attributes)
        ]

    def departments(self, search_filter: str = '(objectClass=organizationalUnit)', attributes: List[str] = ['ou', 'cn']):
        '''Return the list of departments'''
        res = self.query(app.config['LDAP_DEPARTMENT_BASE'], search_filter, attributes)
        if not res:
            return []
        else:
            return [Department(department_number=int(dept['attributes']['ou'][0]),
                               name=dept['attributes']['cn'][0])
                    for dept in res]


def sync_departments(ldap: LDAP, deletion_threshold: int = -1, ignore_deletion_threshold: bool = False):
    '''Update the department table'''
    db_departments = Department.query.all()
    ldap_departments = dict((dep.department_number, dep) for dep in ldap.departments())
    # handle renamed or deleted departments
    for ddep in db_departments:
        ldep = ldap_departments.get(ddep.department_number)
        if ldep:
            if ddep.name != ldep.name:
                logging.info('Renaming department %s to %s' % (ddep.name, ldep.name))
                ddep.name = ldep.name
            del ldap_departments[ddep.department_number]
        else:
            logging.info('Deleting department %s' % ddep.name)
            db.session.delete(ddep)
    if not ignore_deletion_threshold:
        check_deletion_threshold(Department, deletion_threshold)
    # handle new departments
    for ldep in list(ldap_departments.values()):
        logging.info('Creating department %s' % ldep.name)
        db.session.add(ldep)


def log_stdout(message: str):
    logging.info(message)
    print(message)


def check_deletion_threshold(instance_type: type, threshold: int = -1):
    if threshold >= 0:
        deleted_elements = [e for e in db.session.deleted if isinstance(e, instance_type)]
        if len(deleted_elements) > threshold:
            msg = 'Number of %s deletions (%s) above threshold (%s), aborting sync.' % (instance_type.__name__, len(deleted_elements), threshold)
            logging.exception(msg)
            raise Exception(msg)


def sync_users(ldap: LDAP, deletion_threshold: int = -1, ignore_deletion_threshold: bool = False):
    """
    Synchronizes the local User database table with the LDAP server.
    Supports both standard users and pseudo-users by querying their respective Base DNs
    and updating fields including ldap_cn, ldap_uid, department_number, and is_pseudo.
    Whether a user is LDAP-synced is determined by checking the database column ldap_uid.
    If it is not None, the user is from LDAP and will be deleted if deleted in LDAP.

    :param ldap: The active LDAP connection instance.
    :param deletion_threshold: Maximum allowed deletions before aborting the sync.
    :param ignore_deletion_threshold: If True, bypasses the deletion limit check.
    """
    db_users_all = User.query.all()

    # We sync all database users (allowing newly logged-in users with ldap_uid=None to be populated)
    db_users = db_users_all

    # Maps username (str) to the updated User object fetched from LDAP
    ldap_users: Dict[str, User] = {}

    # Separate database users to optimize LDAP query performance and prevent unnecessary load
    standard_db_users = [u for u in db_users if u.is_pseudo is not True]
    pseudo_db_users = [u for u in db_users if u.is_pseudo is not False]

    # --- Query Standard Users ---
    try:
        search_filter = "(|%s)" % "".join(
            "(o=%s)" % u.username for u in standard_db_users
        )
        for u in ldap.users(search_filter):
            u.is_pseudo = False
            ldap_users[u.username] = u
    except Exception as e:
        logging.error(
            f"While performing ldap search for normal user we encountered the following exception: {e}"
        )
        raise e

    # --- Query Pseudo/Service Users ---
    pseudo_base = app.config["LDAP_PSEUDO_USER_BASE"]
    if pseudo_base and pseudo_base != app.config["LDAP_USER_BASE"]:
        try:
            search_filter = "(|%s)" % "".join(
                "(uid=%s)" % u.username for u in pseudo_db_users
            )
            for u in ldap.users(
                search_filter, base_dn=app.config["LDAP_PSEUDO_USER_BASE"]
            ):
                u.is_pseudo = True
                ldap_users[u.username] = u
        except Exception as e:
            logging.error(
                f"While performing ldap search for pseudo users we encountered the following exception: {e}"
            )
            raise e
    else:
        logging.info("Skipped pseudo user sync.")

    # --- Reconcile Database with LDAP Results ---
    for db_user in db_users:
        ldap_user = ldap_users.get(db_user.username)
        if ldap_user:
            if db_user.ldap_cn != ldap_user.ldap_cn:
                logging.info('User %s changed cn from %s to %s' %
                             (db_user.username,
                              db_user.ldap_cn,
                              ldap_user.ldap_cn))
                db_user.ldap_cn = ldap_user.ldap_cn
            if db_user.department_number != ldap_user.department_number:
                logging.info('User %s moved from department_number %s to %s' %
                             (db_user.username,
                              db_user.department_number,
                              ldap_user.department_number))
                db_user.department_number = ldap_user.department_number
            if db_user.ldap_uid != ldap_user.ldap_uid:
                logging.info('User %s changed uid from %s to %s' %
                             (db_user.username,
                              db_user.ldap_uid,
                              ldap_user.ldap_uid))
                db_user.ldap_uid = ldap_user.ldap_uid
            if db_user.is_pseudo != ldap_user.is_pseudo:
                logging.info(
                    "User %s changed is_pseudo from %s to %s"
                    % (db_user.username, db_user.is_pseudo, ldap_user.is_pseudo)
                )
                db_user.is_pseudo = ldap_user.is_pseudo
        elif db_user.ldap_uid:  # make sure to only delete users that are ldap_synced
            log_stdout("Deleting user %s" % db_user.username)
            db.session.delete(db_user)
    if not ignore_deletion_threshold:
        check_deletion_threshold(User, deletion_threshold)


@time_function
@transaction
def add_user(username: str):
    """
    Manually imports a single user from LDAP into the DIM database.
    Attempts to locate the user in the standard LDAP_USER_BASE first,
    and falls back to LDAP_PSEUDO_USER_BASE if configured.

    :param username: The exact username (attribute 'o') of the LDAP entry.
    """
    # Check if the user already exists in the database
    try:
        _ = User.query.filter(User.username == username).one()
        log_stdout('User %s already present' % username)
        return
    except NoResultFound:
        try:
            ldap_user = None
            ldap = LDAP()

            # 1. Search in the standard user base
            standard_search = ldap.users(f"(o={username})")
            if standard_search:
                ldap_user = standard_search[0]
                ldap_user.is_pseudo = False
            # 2. Fallback: Search in the pseudo user base (if configured)
            elif app.config.get("LDAP_PSEUDO_USER_BASE"):
                pseudo_search = ldap.users(
                    f"(o={username})", base_dn=app.config["LDAP_PSEUDO_USER_BASE"]
                )
                if pseudo_search:
                    ldap_user = pseudo_search[0]
                    ldap_user.is_pseudo = True
        except Exception as e:
            logging.exception(
                f"LDAP error occurred during manual import for user {username}. Got the following exception: {e}"
            )
            log_stdout(
                f"Error: Failed to connect to LDAP server or query failed. Please check LDAP server status. Got the following exception: {e}"
            )
            return

        # Save to database if the user was found in either LDAP base
        if ldap_user:
            db.session.add(ldap_user)
            log_stdout('Added user %s' % username)
            ldap_user.register()
            log_stdout('Added user %s to user-group all_users' % username)
        else:
            log_stdout('User %s not in LDAP' % username)


@time_function
@transaction
def ldap_sync(
    ignore_deletion_threshold: bool = False, cleanup_department_groups: bool = False
):
    """Update Users, Pseudo Users, Group, and Departments from LDAP"""
    ldap = LDAP()
    deletion_thresholds = app.config.get_namespace('LDAP_SYNC_DELETION_THRESHOLD_')

    if sys.stdout.isatty():
        logging.getLogger().addHandler(logging.StreamHandler(sys.stderr))

    sync_departments(ldap, int(deletion_thresholds.get('departments', -1)), ignore_deletion_threshold)
    sync_users(ldap, int(deletion_thresholds.get('users', -1)), ignore_deletion_threshold)

    # Synchronize group members
    ldap_users = {}  # map department_number to list of usernames
    for group in Group.query.filter(Group.department_number != None).all():  # noqa
        search_results = ldap.departments('(ou=%s)' % group.department_number)
        if len(search_results) == 0:
            group.department_number = None
            log_stdout('Department %s %s was deleted and had the following members from LDAP: %s' % (
                group.department_number,
                group.name,
                ' '.join(gm.user.username for gm in GroupMembership.query
                         .filter(GroupMembership.from_ldap)
                         .filter(GroupMembership.group == group).all())))
        else:
            dept = search_results[0]
            if dept.name != group.name:
                new_name = dept.name
                if Group.query.filter(Group.name == new_name).count():
                    # DIM-209 append id to department name to generate an unique user group name
                    new_name += '_%s' % dept.department_number
                logging.info('Renaming group %s to %s' % (group.name, new_name))
                group.name = new_name
            ldap_users[group.department_number] = \
                [u.username for u in ldap.users('(departmentNumber=%s)' % dept.department_number)]
    # Remove all users added by a ldap query that are no longer present in the group
    for membership in GroupMembership.query.filter(GroupMembership.from_ldap).all():  # noqa
        if membership.group.department_number is None or \
           membership.user.username not in ldap_users[membership.group.department_number]:
            logging.info('User %s was removed from group %s' %
                         (membership.user.username, membership.group.name))
            membership.group.remove_user(membership.user)
    # Remove users in department user-groups, that have not been added via ldap
    if cleanup_department_groups:
        for membership in GroupMembership.query.filter(GroupMembership.from_ldap == False).filter(Group.department_number != None).filter(GroupMembership.usergroup_id==Group.id).all():
            if membership.user.username not in ldap_users[membership.group.department_number]:
                logging.info(f'User {membership.user.username} was removed from department group {membership.group.name} ({membership.group.department_number}) as the membership was not from LDAP')
                membership.group.remove_user(membership.user)

    # Add new users to groups
    for group in Group.query.filter(Group.department_number != None).all():  # noqa
        group_users = set([u.username for u in group.users])
        for username in [u for u in ldap_users[group.department_number] if u not in group_users]:
            user = User.query.filter_by(username=username).first()
            if user is None:
                ldap_search = ldap.users('(o=%s)' % username)
                if ldap_search:
                    lu = ldap_search[0]
                    user = User(username=username,
                                ldap_uid=lu.ldap_uid,
                                ldap_cn=lu.ldap_cn,
                                department_number=lu.department_number)
                    db.session.add(user)
                    db.session.add(GroupMembership(user=user, group=group, from_ldap=True))
                    group_users.add(username)
                    logging.info('User %s was created and added to group %s', username, group.name)
            else:
                logging.info('User %s was added to group %s', username, group.name)
                db.session.add(GroupMembership(user=user, group=group, from_ldap=True))
                group_users.add(username)
