

import re

import dns.rdata
from dim.errors import InvalidParameterError
from dim.messages import Messages
from dim.util import make_fqdn
from typing import Tuple, List, Union


def label_is_valid(label):
    if (len(label) == 0 or
            len(label) > 63 or
            label.startswith('-') or
            label.endswith('-') or
            not re.match(r'^[_a-z0-9-/]+$', label)):
        return False
    return True


def validate_fqdn(self, key, value, **kwargs):
    if value == '.':
        return value
    if not value.endswith('.'):
        raise InvalidParameterError('Invalid %s: %s' % (key, value))
    if len(value) > 254:
        raise InvalidParameterError('Field %s exceeds maximum length 254' % key)
    value = value.lower()
    for label in value.split('.')[:-1]:
        if not label_is_valid(label):
            raise InvalidParameterError('Invalid %s: %s' % (key, value))
    return value


def validate_mail(self, key, value, **kwargs):
    if '.' not in value[:-1]:
        raise InvalidParameterError('Invalid %s: %s' % (key, value))
    try:
        tmp = value.replace('\\.', '.')
        validate_fqdn(None, key, tmp)
    except:
        raise InvalidParameterError('Invalid %s: %s' % (key, value))
    return value.lower()


def validate_target(self, key, value, preference=None, **kwargs):
    if len(value) > 254:
        raise InvalidParameterError('Invalid %s: %s' % (key, value))
    value = value.lower()
    if preference == 0 and value == '.':
        return value
    dnlabels = value.split('.')
    for label in dnlabels[:-1]:
        if not label_is_valid(label):
            raise InvalidParameterError('Invalid %s: %s' % (key, value))
    # the last dn-label is empty for fqdns
    if len(dnlabels[-1]):
        if not label_is_valid(dnlabels[-1]):
            raise InvalidParameterError('Invalid %s: %s' % (key, value))
    return value


def validate_uint8(self, key, value, **kwargs):
    value = int(value)
    if value < 0 or value > 2 ** 8 - 1:
        raise InvalidParameterError("Invalid %s: %d" % (key, value))
    return value


def validate_uint16(self, key, value, **kwargs):
    value = int(value)
    if value < 0 or value > 2 ** 16 - 1:
        raise InvalidParameterError("Invalid %s: %d" % (key, value))
    return value


def validate_uint32(self, key, value, **kwargs):
    value = int(value)
    if value < 0 or value > 2 ** 32 - 1:
        raise InvalidParameterError("Invalid %s: %d" % (key, value))
    return value


def validate_preference(self, key, value, exchange=None):
    value = int(value)
    if (value == 0 and exchange == '.'):
        return value
    if (value < 1 or value > 32767):
        raise InvalidParameterError('Preference min 1 max 32767 or 0 with exchange "."')
    return value


def validate_certificate(self, key, value, **kwargs):
    if ' ' in value:
        raise InvalidParameterError("Invalid %s: %s" % (key, value))
    return value


def validate_hexstring(self, key, value, **kwargs):
    if ' ' in value or not re.match(r'^[0-9a-fA-F]*$', value) or len(value) % 2 != 0:
        raise InvalidParameterError("Invalid %s: %s" % (key, value))
    return value


def validate_enum(enum, reserved=None):
    def f(self, key, value, **kwargs):
        try:
            value = int(value)
        except:
            value = value.upper()
        if type(value) == int:
            if reserved and value in reserved:
                raise InvalidParameterError("Invalid %s: %s" % (key, value))
            if value not in list(enum.values()):
                Messages.warn('%s value %s is unassigned' % (key, value))
            return validate_uint8(self, key, value)
        else:
            if value in enum:
                return enum[value]
            else:
                raise InvalidParameterError("Invalid %s: %s" % (key, value))
    return f


def validate_caa_flags(self, key, value, **kwargs):
    try:
        value = int(value)
        if value not in (0, 1, 128):
            raise Exception()
        return value
    except:
        raise InvalidParameterError('CAA Issuer critical only allows values 0, 1, 128')


def validate_property_tag(self, key, value, **kwargs):
    value = value.lower()
    if value not in ("issue", "issuewild", "iodef"):
        raise InvalidParameterError('only CAA property tags "issue", "issuewild", "iodef" are allowed')
    return value


def _unescapify_from(value, offset: int) -> Tuple[bytes, int]:
    '''
    Unescape dns character string in value[offset:]. Returns a tuple of (bytearray unescaped_string,
    the position where the processing of value stopped).

    If offset == 0, value is supposed to be a dns character string. In this case, an exception is
    thrown at the first unescaped double quote found.

    If offset > 0, value should be a concatenation of dns character strings enclosed in double
    quotes (eg. '"ab" "c"').

    The processing of value stops at the first unescaped double quote. This position is returned
    along with the unescaped string created thus far.
    '''
    string = b''
    i = offset
    while i < len(value):
        if value[i] == '"':
            if offset > 0:
                return string, i
            raise ValueError('Unescaped quote at position %d in: %s' % (i, value))
        elif value[i] == '\\':
            try:
                escape_sequence = value[i]
                if value[i + 1] in ('"', '\\'):
                    # escaped " or /
                    escape_sequence = value[i:i + 2]
                    string += str(value[i + 1]).encode('utf-8')
                    i += 2
                else:
                    # assume escaped ordinal value (int) with a fixed length of 3
                    # e.g. \097 for the character a
                    escape_sequence = value[i:i + 4]
                    string += bytes((int(value[i + 1:i + 4]),))
                    i += 4
            except:
                raise ValueError('Invalid escape sequence: %s' % escape_sequence)
        else:
            if 32 <= ord(value[i]) <= 126:
                string += value[i].encode('utf-8')
                i += 1
            else:
                raise ValueError('Invalid character at position %d in: %s' % (i, value))
    return string, len(value)


def _unescapify(value):
    s, _ = _unescapify_from(value, 0)
    return s


def _parse_strings(value):
    strings = []
    i = 0
    while i < len(value):
        if value[i].isspace():
            i += 1
        elif value[i] == '"':
            string, end = _unescapify_from(value, i + 1)
            if end == len(value):
                raise ValueError('Expected " at the end of: %s' % value)
            strings.append(string)
            i = end + 1
        else:
            raise ValueError('Unexpected characters outside quotes at position %s in: %s' % (i, value))
    return strings


def validate_strings(self, key, value: Union[str, List[str]], **kwargs):
    if isinstance(value, str):
        strings = _parse_strings(value)
    elif isinstance(value, list):
        strings = [_unescapify(v) for v in value]
    else:
        raise ValueError('%s must be either a string or a list of strings' % key)
    # Split strings longer than 255
    split_strings = []
    splits = 0
    for s in strings:
        while len(s) > 255:
            splits += 1
            split_strings.append(s[:255])
            s = s[255:]
        if s:
            split_strings.append(s)
    if splits:
        Messages.warn('A TXT record string was longer than 255 characters, it was automatically divided.')
    return ' '.join('"' + dns.rdata._escapify(s) + '"' for s in split_strings)


def validate_character_string(self, key, value, **kwargs):
    if len(_unescapify(value)) > 255:
        raise ValueError('Invalid %s: character string too long' % key)
    return '"%s"' % value


class RRMeta():
    pass

class RRType(RRMeta):
    target_fields = ('ptrdname', 'cname', 'exchange', 'nsdname', 'target', 'txtdname')
    mbox_fields = ('mbox', )
    string_fields = ('cpu', 'os', 'flags', 'service', 'regexp', 'property_value')
    int_fields = ('preference', 'priority', 'weight', 'port', 'certificate_type', 'key_tag', 'algorithm', 'order',
                  'certificate_usage', 'selector', 'matching_type', 'fingerprint_type', 'digest_type', 'caa_flags')

    @classmethod
    def value_from_fields(self, **kwargs):
        return ' '.join(str(kwargs[f]) for f in self.fields)

    @classmethod
    def fields_from_value(cls, value):
        fields = dict(list(zip(cls.fields, value.split())))
        string_fields = set(cls.fields) & set(RRType.string_fields)
        if string_fields:
            for field in string_fields:
                fields[field] = fields[field][1:-1]
        int_fields = set(cls.fields) & set(RRType.int_fields)
        if int_fields:
            for field in int_fields:
                fields[field] = int(fields[field])
        if 'strings' in set(cls.fields):
            fields['strings'] = value
        return fields

    @classmethod
    def fqdn_target(cls, value, zone_name):
        target_fields = set(cls.fields) & set(RRType.target_fields)
        if target_fields:
            fields = cls.fields_from_value(value)
            for name in target_fields:
                fields[name] = make_fqdn(fields[name], zone_name)
            return cls.value_from_fields(**fields)
        else:
            return value


class A(RRType):
    fields = ('ip', )
    validate = {}


class AAAA(RRType):
    fields = ('ip', )
    validate = {}


class PTR(RRType):
    fields = ('ptrdname', )
    validate = {'ptrdname': validate_fqdn}


class CNAME(RRType):
    fields = ('cname', )
    validate = {'cname': validate_target}


class DNAME(RRType):
    fields = ('target', )
    validate = {'target': validate_target}


class MX(RRType):
    fields = ('preference', 'exchange')
    validate = {'preference': validate_preference,
                'exchange': validate_target}


class NS(RRType):
    fields = ('nsdname', )
    validate = {'nsdname': validate_target}


class SRV(RRType):
    fields = ('priority', 'weight', 'port', 'target')
    validate = {'priority': validate_uint16,
                'weight': validate_uint16,
                'port': validate_uint16,
                'target': validate_target}


class TXT(RRType):
    fields = ('strings', )
    validate = {'strings': validate_strings}

    @classmethod
    def value_from_fields(self, **kwargs):
        kwargs['strings'] = validate_strings(None, 'strings', kwargs['strings'])
        return kwargs['strings']


class SPF(TXT):
    pass


class RP(RRType):
    fields = ('mbox', 'txtdname')
    validate = {'mbox': validate_mail,
                'txtdname': validate_fqdn}


class CERT(RRType):
    fields = ('certificate_type', 'key_tag', 'algorithm', 'certificate')
    validate = {'certificate_type': validate_uint16,
                'key_tag': validate_uint16,
                'algorithm': validate_uint8,
                'certificate': validate_certificate}


class HINFO(RRType):
    fields = ('cpu', 'os')
    validate = {'cpu': validate_character_string,
                'os': validate_character_string}


class NAPTR(RRType):
    fields = ('order', 'preference', 'flags', 'service', 'regexp', 'replacement')
    validate = {'order': validate_uint16,
                'preference': validate_uint16,
                'flags': validate_character_string,
                'service': validate_character_string,
                'regexp': validate_character_string,
                'replacement': validate_fqdn}


class TLSA(RRType):
    fields = ('certificate_usage', 'selector', 'matching_type', 'certificate')
    validate = {'certificate_usage': validate_enum({'PKIX-TA': 0, 'PKIX-EE': 1, 'DANE-TA': 2, 'DANE-EE': 3, 'PRIVCERT': 255}),
                'selector': validate_enum({'CERT': 0, 'SPKI': 1, 'PRIVSEL': 255}),
                'matching_type': validate_enum({'FULL': 0, 'SHA2-256': 1, 'SHA2-512': 2, 'PRIVMATCH': 255}),
                'certificate': validate_hexstring}


class SSHFP(RRType):
    fields = ('algorithm', 'fingerprint_type', 'fingerprint')
    validate = {'algorithm': validate_enum({'RSA': 1, 'DSS': 2, 'ECDSA': 3}, reserved=[0]),
                'fingerprint_type': validate_enum({'SHA-1': 1, 'SHA-256': 2}, reserved=[0]),
                'fingerprint': validate_hexstring}


class DS(RRType):
    fields = ('key_tag', 'algorithm', 'digest_type', 'digest')
    validate = {'key_tag': validate_uint16,
                'algorithm': validate_uint8,
                'digest_type': validate_uint8,
                'digest': validate_hexstring}


class CAA(RRType):
    fields = ('caa_flags', 'property_tag', 'property_value')
    validate = {'caa_flags': validate_caa_flags,
                'property_tag': validate_property_tag,
                'property_value': validate_character_string}
