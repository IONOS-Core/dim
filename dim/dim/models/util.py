import datetime

from flask import g
from sqlalchemy import Column, String, TIMESTAMP
from sqlalchemy.orm import contains_eager

from dim import db


def get_session_username():
    return getattr(g, 'username', 'local')


def get_session_tool():
    return getattr(g, 'tool', None)


def get_session_ip_address():
    return getattr(g, 'ip_address', None)


def get_session_ip_version():
    return getattr(g, 'ip_version', None)


class WithAttr(object):
    '''
    Mixin to add methods for handling attributes to Ipblock and Pool

    The class or instance inheriting the mixin must have the following special
    attributes:

    - AttrNameClass = model class for attribute names
    - AttrClass = model class for attribute values
    - attr_backref = the name of the relation from AttrClass
    '''
    def set_attrs(self, attributes):
        from .history import record_history
        if not attributes:
            return
        if not isinstance(attributes, dict):
            raise Exception("Attributes must be a map")
        # Reject reserved attributes
        keys = list(attributes.keys())
        for name in keys:
            if name in self.AttrNameClass.reserved:
                raise Exception("The attribute name '%s' is reserved" % name)
            if name.startswith('-'):
                raise Exception("Attribute names may not contain a leading '-'.")

        existing_names = db.session.query(self.AttrNameClass)\
            .filter(self.AttrNameClass.name.in_(keys)).all()
        current = self.AttrClass.query\
            .filter_by(**{self.attr_backref: self})\
            .join(self.AttrNameClass)\
            .options(contains_eager(self.AttrClass.name))\
            .filter(self.AttrNameClass.name.in_(list(attributes.keys()))).all()
        names = dict((name.name, name) for name in existing_names)
        # Create missing AttrNames
        for name in set(keys) - set(names.keys()):
            names[name] = self.AttrNameClass(name=name)
            db.session.add(names[name])
        # Set the values
        old_values = {}
        name2attr = dict((attr.name.name, attr) for attr in current)
        for name, value in list(attributes.items()):
            if name in name2attr:
                old_values[name] = name2attr[name].value
                name2attr[name].value = value
            else:
                db.session.add(self.AttrClass(name=names[name],
                                              value=value,
                                              **{self.attr_backref: self}))
        self.update_modified()
        for k, v in list(attributes.items()):
            old_value = old_values.get(k, None)
            if v != old_value:
                record_history(self, action='set_attr', attrname=k, newvalue=v, oldvalue=old_value)

    def delete_attrs(self, attribute_names):
        from .history import record_history
        if not attribute_names:
            return
        for name in attribute_names:
            if ':' in name:
                raise Exception("Attribute name '%s' contains ':'. Did you mean to specify only the attribute name?" % name)
        current = self.AttrClass.query\
            .filter_by(**{self.attr_backref: self})\
            .join(self.AttrNameClass)\
            .filter(self.AttrNameClass.name.in_(attribute_names)).all()
        for attr in current:
            db.session.delete(attr)
        self.update_modified()
        for attr in attribute_names:
            record_history(self, action='del_attr', attrname=attr)

    def get_attrs(self):
        return dict(db.session.query(self.AttrNameClass.name, self.AttrClass.value)
                    .filter(getattr(self.AttrClass, self.attr_backref) == self)
                    .filter(self.AttrClass.name_id == self.AttrNameClass.id).all())


class TrackChanges(object):
    created = Column(TIMESTAMP, nullable=False, default=datetime.datetime.utcnow,
                     server_default='1970-01-02 00:00:01')
    created_by = Column(String(128), default=get_session_username)
    modified = Column(TIMESTAMP, nullable=False, default=datetime.datetime.utcnow,
                      server_default='1970-01-02 00:00:01')
    modified_by = Column(String(128), default=get_session_username)

    def update_modified(self):
        '''Update the modified and modified_by fields.'''
        self.modified = datetime.datetime.utcnow()
        self.modified_by = get_session_username()
