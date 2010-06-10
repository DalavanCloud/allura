# -*- coding: utf-8 -*-
try:
    from setuptools import setup, find_packages
except ImportError:
    from ez_setup import use_setuptools
    use_setuptools()
    from setuptools import setup, find_packages

from pyforge.version import __version__

setup(
    name='pyforge',
    version=__version__,
    description='',
    author='',
    author_email='',
    #url='',
    install_requires=[
        "TurboGears2 >= 2.1a1",
        "PasteScript",
        "Babel >= 0.9.4",
        "Lamson",
        "Carrot",
        "pymongo >= 1.5, < 1.6",
        "pysolr",
        "repoze.what-quickstart",
        "sqlalchemy-migrate",
        "Markdown >= 2.0.3",
        "Pygments >= 1.1.1",
        "PyYAML >= 3.09",
        "python-openid >= 2.2.4",
        "python-ldap == 2.3.9",
        "python-dateutil >= 1.4.1",
        "WebOb == 0.9.8",
        "WebTest == 0.9.8",
        "python-oembed >= 0.1.1",
        "EasyWidgets >= 0.1.1",
        "PIL >= 1.1.7",
        "iso8601",
        "sf.phpsession==0.1",
        "chardet==1.0.1"
        ],
    setup_requires=["PasteScript >= 1.7"],
    paster_plugins=['PasteScript', 'Pylons', 'TurboGears2', 'tg.devtools', 'ForgeMail'],
    packages=find_packages(exclude=['ez_setup']),
    include_package_data=True,
    test_suite='nose.collector',
    tests_require=['WebTest >= 1.2', 'BeautifulSoup'],
    package_data={'pyforge': ['i18n/*/LC_MESSAGES/*.mo',
                                 'templates/*/*',
                                 'public/*/*']},
    message_extractors={'pyforge': [
            ('**.py', 'python', None),
            ('templates/**.mako', 'mako', None),
            ('templates/**.html', 'genshi', None),
            ('public/**', 'ignore', None)]},

    entry_points="""
    [paste.app_factory]
    main = pyforge.config.middleware:make_app
    tool_test = pyforge.config.middleware:make_tool_test_app

    [paste.app_install]
    main = pylons.util:PylonsInstaller
    tool_test = pylons.util:PylonsInstaller

    [paste.paster_create_template]
    forgeapp=pyforge.pastetemplate:ForgeAppTemplate

    [pyforge]
    profile = pyforge.ext.user_profile:UserProfileApp
    admin = pyforge.ext.admin:AdminApp
    search = pyforge.ext.search:SearchApp
    home = pyforge.ext.project_home:ProjectHomeApp
    sfx = pyforge.ext.sfx:SFXApp

    [pyforge.auth]
    local = pyforge.lib.plugin:LocalAuthenticationProvider
    ldap = pyforge.lib.plugin:LdapAuthenticationProvider
    sfx = pyforge.ext.sfx:SFXAuthenticationProvider

    [pyforge.project_registration]
    local = pyforge.lib.plugin:LocalProjectRegistrationProvider
    sfx = pyforge.ext.sfx:SFXProjectRegistrationProvider

    [flyway.migrations]
    pyforge = pyforge.migrations

    [flyway.test_migrations]
    a = flyway.tests.migrations_a
    b = flyway.tests.migrations_b

    [paste.paster_command]
    reactor_setup = pyforge.command:ReactorSetupCommand
    reactor = pyforge.command:ReactorCommand
    sendmsg = pyforge.command:SendMessageCommand
    models = pyforge.command:ShowModelsCommand
    reindex = pyforge.command:ReindexCommand
    script = pyforge.command:ScriptCommand
    flyway = flyway.command:MigrateCommand
    
    [easy_widgets.resources]
    ew_resources=pyforge.config.resources:register_ew_resources

    """,
)

