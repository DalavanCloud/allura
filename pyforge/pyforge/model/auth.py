import logging
import os
from base64 import b64encode
from random import randint
from hashlib import sha256

import ldap
from pylons import c, g
from tg import config

from ming.orm.ormsession import ThreadLocalORMSession
from ming.orm.mapped_class import MappedClass
from ming.orm.property import FieldProperty
from ming import schema as S

from pyforge.lib.helpers import push_config
from .session import ProjectSession
from .session import main_doc_session, main_orm_session
from .session import project_doc_session, project_orm_session


log = logging.getLogger(__name__)

SALT_LENGTH=8

def encode_password(password, salt=None):
    if salt is None:
        salt = ''.join(chr(randint(1, 0x7f))
                       for i in xrange(SALT_LENGTH))
    hashpass = sha256(salt + password.encode('utf-8')).digest()
    return 'sha256' + salt + b64encode(hashpass)

class EmailAddress(MappedClass):
    class __mongometa__:
        name='email_address'
        session = main_orm_session

    _id = FieldProperty(str)
    claimed_by_user_id=FieldProperty(S.ObjectId, if_missing=None)
    confirmed = FieldProperty(bool)
    nonce = FieldProperty(str)

    def claimed_by_user(self):
        return User.query.get(_id=self.claimed_by_user_id)

    @classmethod
    def upsert(cls, addr):
        addr = cls.canonical(addr)
        result = cls.query.get(_id=addr)
        if not result:
            result = cls(_id=addr)
        return result

    @classmethod
    def canonical(cls, addr):
        user, domain = addr.split('@')
        return '%s@%s' % (user, domain.lower())

    def send_verification_link(self):
        self.nonce = sha256(os.urandom(10)).hexdigest()
        log.info('Would send verification link to %s', self._id)
        text = '''
To verify the email address %s belongs to the user %s,
please visit the following URL:

    %s
''' % (self._id, self.claimed_by_user().username, g.url('/auth/verify_addr', a=self.nonce))
        log.info('Verification email:\n%s', text)
    
class OpenId(MappedClass):
    class __mongometa__:
        name='openid'
        session = main_orm_session

    _id = FieldProperty(str)
    claimed_by_user_id=FieldProperty(S.ObjectId, if_missing=None)
    display_identifier=FieldProperty(str)

    @classmethod
    def upsert(cls, url, display_identifier):
        result = cls.query.get(_id=url)
        if not result:
            result = cls(
                _id=url,
                display_identifier=display_identifier)
        return result

    def claimed_by_user(self):
        if self.claimed_by_user_id:
            result = User.query.get(_id=self.claimed_by_user_id)
        else: # pragma no cover
            result = User.register(
                dict(username=None, password=None,
                     display_name=self.display_identifier,
                     open_ids=[self._id]),
                make_project=False)
            self.claimed_by_user_id = result._id
        return result
            
class User(MappedClass):
    SALT_LEN=8
    class __mongometa__:
        name='user'
        session = main_orm_session
        unique_indexes = [ 'username' ]

    _id=FieldProperty(S.ObjectId)
    username=FieldProperty(str)
    display_name=FieldProperty(str)
    open_ids=FieldProperty([str])
    email_addresses=FieldProperty([str])
    password=FieldProperty(str)
    projects=FieldProperty([S.ObjectId])
    preferences=FieldProperty(dict(
            email_address=str,
            email_format=str))
    plugin_preferences=FieldProperty({str:{str:None}}) # full mount point: prefs dict

    def url(self):
        return '/users/' + self.username + '/'

    def address_object(self, addr):
        return EmailAddress.query.get(_id=addr, claimed_by_user_id=self._id)

    def openid_object(self, oid):
        return OpenId.query.get(_id=oid, claimed_by_user_id=self._id)

    def claim_openid(self, oid_url):
        oid_obj = OpenId.upsert(oid_url, self.display_name)
        oid_obj.claimed_by_user_id = self._id
        if oid_url in self.open_ids: return
        self.open_ids.append(oid_url)

    def claim_address(self, email_address):
        addr = EmailAddress.canonical(email_address)
        email_addr = EmailAddress.upsert(addr)
        email_addr.claimed_by_user_id = self._id
        if addr in self.email_addresses: return
        self.email_addresses.append(addr)

    @classmethod
    def register(cls, doc, make_project=True):
        from pyforge import model as M
        method = config.get('auth.method', 'local')
        if method == 'local':
            result = cls._register_local(doc)
        elif method == 'ldap': # pragma no cover
            result = cls._register_ldap(doc)
        if make_project:
            n = M.Neighborhood.query.get(name='Users')
            n.register_project('users/' + result.username, result)
        return result

    @classmethod
    def _register_local(cls, doc):
        return cls(**doc)

    @classmethod
    def _register_ldap(cls, doc):
        password = doc.pop('password', None)
        result = cls(**doc)
        dn = 'uid=%s,%s' % (doc['username'], config['auth.ldap.suffix'])
        try:
            con = ldap.initialize(config['auth.ldap.server'])
            con.bind_s(config['auth.ldap.admin_dn'],
                       config['auth.ldap.admin_password'])
            ldap_info = dict(
                uid=doc['username'],
                displayName=doc['display_name'],
                cn=doc['display_name'],
                userPassword=password,
                objectClass=['inetOrgPerson'],
                givenName=doc['display_name'].split()[0],
                sn=doc['display_name'].split()[-1])
            ldap_info = dict((k,v) for k,v in ldap_info.iteritems()
                             if v is not None)
            try:
                con.add_s(dn, ldap_info.items())
            except ldap.ALREADY_EXISTS:
                con.modify_s(dn, [(ldap.MOD_REPLACE, k, v)
                                  for k,v in ldap_info.iteritems()])
            con.unbind_s()
        except:
            raise
        return result

    def private_project(self):
        from .project import Project
        return Project.query.get(shortname='users/%s' % self.username)

    @property
    def script_name(self):
        return '/projects/users/' + self.username + '/'

    def my_projects(self):
        from .project import Project
        for p in self.projects:
            yield Project.query.get(_id=p)

    def role_iter(self):
        yield ProjectRole.query.get(name='*anonymous')
        if self._id:
            yield ProjectRole.query.get(name='*authenticated')
        if self._id:
            pr = self.project_role()
            for role in pr.role_iter():
                yield role

    def project_role(self, project=None):
        if project is None: project = c.project
        with push_config(c, project=project, user=self):
            if self._id is None:
                return ProjectRole.query.get(name='*anonymous')
            obj = ProjectRole.query.get(user_id=self._id)
            if obj is None:
                obj = ProjectRole(user_id=self._id)
                self.projects.append(c.project._id)
            return obj

    def set_password(self, password):
        method = config.get('auth.method', 'local')
        if method == 'local':
            return self._set_password_local(password)
        elif method == 'ldap':
            return self._set_password_ldap(password)

    def _set_password_local(self, password):
        self.password = encode_password(password)

    def _set_password_ldap(self, password):
        try:
            dn = 'uid=%s,%s' % (self.username, config['auth.ldap.suffix'])
            con = ldap.initialize(config['auth.ldap.server'])
            con.bind_s(dn, password)
            con.modify_s(dn, [(ldap.MOD_REPLACE, 'userPassword', password)])
            con.unbind_s()
        except ldap.INVALID_CREDENTIALS:
            return False
        
    def validate_password(self, password):
        method = config.get('auth.method', 'local')
        if method == 'local':
            return self._validate_password_local(password)
        elif method == 'ldap':
            return self._validate_password_ldap(password)

    def _validate_password_ldap(self, password):
        try:
            dn = 'uid=%s,%s' % (self.username, config['auth.ldap.suffix'])
            con = ldap.initialize(config['auth.ldap.server'])
            con.bind_s(dn, password)
            con.unbind_s()
        except ldap.INVALID_CREDENTIALS:
            return False
        return True
    
    def _validate_password_local(self, password):
        if not self.password: return False
        salt = str(self.password[6:6+self.SALT_LEN])
        check = encode_password(password, salt)
        return check == self.password

    @classmethod
    def anonymous(cls):
        return User.query.get(_id=None)

class ProjectRole(MappedClass):
    class __mongometa__:
        session = project_orm_session
        name='user'
    
    _id = FieldProperty(S.ObjectId)
    name = FieldProperty(str)
    user_id = FieldProperty(S.ObjectId, if_missing=None) # if role is a user
    roles = FieldProperty([S.ObjectId])

    def display(self):
        if self.name: return self.name
        if self.user_id:
            u = self.user
            if u.username: uname = u.username
            elif u.display_name: uname = u.display_name
            else: uname = u._id
            return '*user-%s' % uname
        return '**unknown name role: %s' % self._id # pragma no cover

    @property
    def special(self):
        if self.name:
            return '*' == self.name[0]
        if self.user_id:
            return True
        return False # pragma no cover

    @property
    def user(self):
        return User.query.get(_id=self.user_id)

    def role_iter(self, visited=None):
        if visited is None: visited = set()
        if self._id not in visited: 
            yield self
            visited.add(self._id)
            for rid in self.roles:
                pr = ProjectRole.query.get(_id=rid)
                if pr is None: continue
                for rr in pr.role_iter(visited):
                    yield rr

