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


@manage_dim.cli.command('update-validity')
@click.option('-v', '--verbose', is_flag=True, help='Print details about evaluated and renewed zones')
@click.option('-n', '--dry-run', '--noop', 'dryrun', is_flag=True, help='Do not commit changes to the database')
def update_validity(verbose=False, dryrun=False):
    '''Check for signed zones with less than half of the validity window left and increase the validity period'''
    with current_app.test_request_context():
        g.tid = uuid.uuid4().hex[16:]
        now = datetime.datetime.utcnow()
        all_zones = dim.models.Zone.query.all()
        total_signed = 0
        renewed_count = 0
        skipped_count = 0
        error_count = 0

        for zone in all_zones:
            if not zone.is_signed():
                continue
            total_signed += 1

            half_window = datetime.timedelta(seconds=zone.get_validity_window() / 2)
            reason = None
            if zone.valid_end is None or zone.valid_begin is None:
                reason = "missing timestamps"
            elif zone.valid_end <= zone.valid_begin:
                reason = "corrupted timestamps (valid_end <= valid_begin)"
            elif zone.valid_end <= now + half_window:
                reason = "remaining validity <= 50% of window"

            if reason:
                try:
                    zone.set_validity()
                    # The outputs only re-sign the zone with the new window when they
                    # get a dnssec_update. set_nsec3params() sends one; NSEC zones need
                    # it explicitly, otherwise their signatures expire after one window.
                    if zone.nsec3_algorithm:
                        zone.set_nsec3params(1, zone.nsec3_iterations, zone.nsec3_salt)
                    else:
                        dim.models.OutputUpdate.send_dnssec_update(zone, '')
                    renewed_count += 1
                    if verbose:
                        begin_str = zone.valid_begin.strftime('%Y-%m-%d %H:%M:%SZ') if zone.valid_begin else ''
                        end_str = zone.valid_end.strftime('%Y-%m-%d %H:%M:%SZ') if zone.valid_end else ''
                        click.secho(
                            f"[QUEUE] {zone.name}: {reason} -> "
                            f"valid: {begin_str} to {end_str} "
                            f"(queued in outputupdate)",
                            fg="green",
                        )
                    else:
                        click.secho(f"[QUEUE] {zone.name} ({reason})", fg="green")
                except Exception as e:
                    error_count += 1
                    click.secho(f"[ERROR] Error updating validity for zone {zone.name}: {e}", fg="red")
                    raise
            else:
                skipped_count += 1
                if verbose:
                    end_dt = zone.valid_end
                    remaining = (end_dt - now).total_seconds() / 86400 if end_dt else 0.0
                    half_days = half_window.total_seconds() / 86400
                    end_str = end_dt.strftime('%Y-%m-%d %H:%M:%SZ') if end_dt else 'unknown'
                    click.secho(
                        f"[SKIP]  {zone.name}: remaining {remaining:.1f}d > {half_days:.1f}d "
                        f"(valid until {end_str})",
                        fg="white",
                    )

        if dryrun:
            dim.models.db.session.rollback()
            click.secho("\nDry-run: changes not committed to database and no updates queued.", fg="yellow")
        else:
            dim.models.db.session.commit()

        # Summary output
        unsigned_count = len(all_zones) - total_signed
        click.echo(
            f"\nSummary: Evaluated {total_signed} signed zones ({unsigned_count} unsigned skipped):\n"
            f"  - Queued for pdns-output: {renewed_count}\n"
            f"  - Up-to-date (skipped):   {skipped_count}\n"
            f"  - Errors:                 {error_count}"
        )
        if renewed_count > 0 and not dryrun:
            click.secho(
                "Notice: Validity renewed in DIM database and queued in outputupdate table. "
                "pdns-output daemon will sign and publish records to PowerDNS asynchronously.",
                fg="cyan",
            )


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
