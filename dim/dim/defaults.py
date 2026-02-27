### Database
DB_USERNAME = 'dim'
DB_PASSWORD = 'dim'
DB_HOST     = '127.0.0.1'
SQLALCHEMY_DATABASE_URI = 'mysql://%s:%s@%s:3307/dim' % (DB_USERNAME, DB_PASSWORD, DB_HOST)
DB_LOCK_TIMEOUT = 120


### Authentication
# 'ldap' or None
AUTHENTICATION_METHOD = 'ldap'

LDAP_SERVER = "ldap://ldap-url"
LDAP_USER_DN = "uid=%s"
LDAP_SEARCH_BASE = ""

# Used by manage_dim ldap_sync
LDAP_USER_BASE = ""
LDAP_DEPARTMENT_BASE = ""
LDAP_OPT_TIMEOUT = 60
LDAP_OPT_TIMELIMIT = 60
LDAP_OPT_NETWORK_TIMEOUT = 60
# thresholds for deletions during sync, to help catch configuration/ldap issues
LDAP_SYNC_DELETION_THRESHOLD_USERS = -1
LDAP_SYNC_DELETION_THRESHOLD_DEPARTMENTS = -1


# Set SECRET_KEY to a random string
# The security of this application is compromised if SECRET_KEY is leaked
#SECRET_KEY = 'testkey'
PERMANENT_SESSION_LIFETIME = 10 * 356 * 24 * 3600 # 10 years
TEMPORARY_SESSION_LIFETIME = 24 * 3600            # 1 day

# SSL certificate bundle used for verifying HTTPS connections
REQUESTS_CA_BUNDLE   = '/path/to/ca-bundle.crt'

### Logging
import logging, logging.handlers
LOGGING_LEVEL = logging.INFO
LOGGING_HANDLER = logging.handlers.SysLogHandler(address='/dev/log', facility='local0')
#LOGGING_HANDLER = logging.handlers.TimedRotatingFileHandler('/var/log/dim/dim.log', when='W0', backupCount=10)


### DNS
# Interval to wait before retrying a failed pdns update
PDNS_RETRY_INTERVAL  = 60     # 1 minute

DNS_DEFAULT_REFRESH  = 14400  # 4 hours
DNS_DEFAULT_RETRY    = 3600   # 1 hour
DNS_DEFAULT_EXPIRE   = 605000 # 7 days
DNS_DEFAULT_MINIMUM  = 86400  # 1 day (Min. TTL)
DNS_DEFAULT_ZONE_TTL = 86400  # 1 day (Default. TTL)


### Debugging
SQLALCHEMY_DATABASE_URI_TEST = 'mysql://%s:%s@%s:3307/dim' % (DB_USERNAME, DB_PASSWORD, DB_HOST)
SQLALCHEMY_LOG = False
DEBUG = False
