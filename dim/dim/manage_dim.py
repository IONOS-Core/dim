import datetime
import uuid

from flask import current_app, g, Blueprint
import click

import dim.autodns3
import dim.commands
import dim.ipaddr
import dim.ldap_sync
import dim.models


manage_dim = Blueprint('manage_dim', __name__, cli_group='dim')

@manage_dim.cli.command('rebuild_tree')
def rebuild_tree():
    '''Rebuilds the IP tree parents'''
    dim.commands.rebuild_tree()


@manage_dim.cli.command('set_user')
@click.option('-u', '--username')
@click.option('-t', '--type')
def set_user(username, type):
    '''Sets the user type (Admin or User)'''
    try:
        dim.commands.set_user(username, type)
    except Exception as e:
        print(str(e))


@manage_dim.cli.command('')
def update_validity():
    '''Check for signed zones with less than half of the validity window left and increase the validity period'''
    with current_app.test_request_context():
        g.tid = uuid.uuid4().hex[16:]
        for zone in dim.models.Zone.query.all():
            try:
                if (len(zone.keys) > 0 and
                        (zone.valid_end is None or zone.valid_begin is None or zone.valid_end <= zone.valid_begin or
                         zone.valid_end <= datetime.datetime.utcnow() + (zone.valid_end - zone.valid_begin) / 2)):
                    zone.set_validity()
                    # The outputs only re-sign the zone with the new window when they
                    # get a dnssec_update. set_nsec3params() sends one; NSEC zones need
                    # it explicitly, otherwise their signatures expire after one window.
                    if zone.nsec3_algorithm:
                        zone.set_nsec3params(1, zone.nsec3_iterations, zone.nsec3_salt)
                    else:
                        dim.models.OutputUpdate.send_dnssec_update(zone, '')
            except Exception as e:
                print('Error updating the validity window for zone', zone.name)
                raise
        dim.models.db.session.commit()


@manage_dim.cli.command('ldap_add_user')
@click.option('-u', '--username')
@click.option('-n', '--dry-run', '--noop', 'dryrun',  is_flag=True)
def ldap_sync(dryrun: bool, username: str):
    '''Add User from LDAP'''
    dim.ldap_sync.add_user(dryrun=dryrun, username=username)


@manage_dim.cli.command('ldap_sync')
@click.option('-n', '--dry-run', '--noop', 'dryrun',  is_flag=True)
@click.option('-f', '--ignore-deletion-threshold', 'ignore_deletion_threshold', is_flag=True)
@click.option('--cleanup-department-groups', 'cleanup_department_groups', is_flag=True, help="remove manually added users from department groups")
def ldap_sync(dryrun, ignore_deletion_threshold, cleanup_department_groups):
    '''Update Users, Group, and Departments from LDAP'''
    dim.ldap_sync.ldap_sync(dryrun=dryrun, ignore_deletion_threshold=ignore_deletion_threshold, cleanup_department_groups=cleanup_department_groups)


@manage_dim.cli.command('sync_ldap')
@click.option('-n', '--dry-run', '--noop', 'dryrun',  is_flag=True)
@click.option('-f', '--ignore-deletion-threshold', 'ignore_deletion_threshold', is_flag=True)
@click.option('--cleanup-department-groups', 'cleanup_department_groups', is_flag=True, help="remove manually added users from department groups")
def sync_ldap(dryrun, ignore_deletion_threshold, cleanup_department_groups):
    '''Update Users, Group, and Departments from LDAP'''
    dim.ldap_sync.ldap_sync(dryrun=dryrun, ignore_deletion_threshold=ignore_deletion_threshold, cleanup_department_groups=cleanup_department_groups)


@manage_dim.cli.command('autodns3')
def autodns3():
    dim.autodns3.run()

@manage_dim.cli.command('delete_user')
@click.option('-n', '--dry-run', '--noop', 'dryrun',  is_flag=True)
@click.option('-u', '--username', 'username')
def delete_user(dryrun, username):
    '''Delete user from DIM'''
    dim.commands.delete_user(dryrun=dryrun, username=username)
