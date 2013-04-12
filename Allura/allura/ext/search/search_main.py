#       Licensed to the Apache Software Foundation (ASF) under one
#       or more contributor license agreements.  See the NOTICE file
#       distributed with this work for additional information
#       regarding copyright ownership.  The ASF licenses this file
#       to you under the Apache License, Version 2.0 (the
#       "License"); you may not use this file except in compliance
#       with the License.  You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#       Unless required by applicable law or agreed to in writing,
#       software distributed under the License is distributed on an
#       "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#       KIND, either express or implied.  See the License for the
#       specific language governing permissions and limitations
#       under the License.

import logging

import pkg_resources
from pylons import tmpl_context as c
from tg import expose, validate
from tg.decorators import with_trailing_slash
from formencode import validators as V

from allura.app import Application
from allura import version
from allura.lib import search
from allura.controllers import BaseController

log = logging.getLogger(__name__)

class SearchApp(Application):
    '''This is the HelloWorld application for Allura, showing
    all the rich, creamy goodness that is installable apps.
    '''
    __version__ = version.__version__
    installable = False
    hidden = True
    sitemap=[]

    def __init__(self, project, config):
        Application.__init__(self, project, config)
        self.root = SearchController()
        self.templates = pkg_resources.resource_filename('allura.ext.search', 'templates')

    def main_menu(self): # pragma no cover
        return []

    def sidebar_menu(self): # pragma no cover
        return [ ]

    def admin_menu(self): # pragma no cover
        return []

    def install(self, project):
        pass # pragma no cover

    def uninstall(self, project):
        pass # pragma no cover

class SearchController(BaseController):

    @expose('jinja:allura:templates/search_index.html')
    @validate(dict(q=V.UnicodeString(),
                   history=V.StringBool(if_empty=False)))
    @with_trailing_slash
    def index(self, q=None, history=None, **kw):
        results = []
        count=0
        if not q:
            q = ''
        else:
            pids = [c.project._id] + [
                p._id for p in c.project.subprojects ]
            project_match = ' OR '.join(
                'project_id_s:%s' % pid
                for pid in pids )
            search_query = '%s AND is_history_b:%s AND (%s) AND -deleted_b:true' % (
                q, history, project_match)
            results = search.search(search_query, is_history_b=history, short_timeout=True)
            if results: count=results.hits
        return dict(q=q, history=history, results=results or [], count=count)

