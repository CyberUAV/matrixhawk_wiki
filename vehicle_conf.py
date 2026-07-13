# -*- coding: utf-8 -*-
"""
Shared Sphinx configuration for all 11 vehicle wikis.

Before this module existed, every <vehicle>/source/conf.py carried a full
~400-line copy of the same configuration; the per-wiki delta was exactly
four values. That 11-way duplication is how config drift happened (the
i18n block, logo paths and search-language wiring all had to be patched
eleven times). Now each conf.py is a thin shell:

    import sys
    sys.path.insert(0, '../..')
    import vehicle_conf
    vehicle_conf.apply(globals(), project=u'Copter', shorttitle='copter',
                       favicon='favicon_copter.ico', useralerts=True)

Inheritance note: this file mirrors the structure of the upstream
ArduPilot conf.py, so an upstream conf.py patch is ported HERE once
instead of into eleven files.

Parameters
    project     display name ('Copter', 'Mission Planner', ...)
    shorttitle  wiki dir name — used by the theme's Edit-on-GitHub links,
                the deployed URL segment, and locale_dirs
    favicon     file under images/ (favicon_default.ico for GCS/dev wikis)
    useralerts  vehicles ship _static/useralerts.js (user-alert banner);
                the GCS / dev / umbrella wikis do not
    copy_source None keeps Sphinx's default (True). Only sub leaves the
                default; every other wiki sets False.
"""

import os
import sys


def apply(g, project, shorttitle, favicon='favicon_default.ico',
          useralerts=False, copy_source=False):
    """Populate a conf.py module namespace `g` (pass globals())."""

    import matrixhawk_sphinx_rtd_theme as sphinx_rtd_theme

    # conf.py already put '../..' on sys.path to import this module; keep
    # it there so common_conf and the coverage extension resolve too.
    import common_conf

    # 2019-dec: parameter multi-versioning needs at least 3000;
    # 2020-jun: 15000. Harmless for the small wikis.
    sys.setrecursionlimit(15000)

    # -- General configuration ---------------------------------------------
    g['extensions'] = common_conf.extensions
    g['templates_path'] = ['_templates']
    g['source_suffix'] = '.rst'
    g['master_doc'] = 'index'

    g['project'] = project
    g['copyright'] = (u'2024, ArduPilot Dev Team. '
                      u'Modifications and New Content © 2025, BZUAV Devteam')
    g['author'] = u'BZUAV Dev Team'

    g['exclude_patterns'] = []
    g['pygments_style'] = 'sphinx'
    g['todo_include_todos'] = True

    # Translated named references (`构建 Wiki`_ for `Build the Wiki`_) are
    # the CORRECT way to translate implicit-target refs; Sphinx remaps them
    # positionally and the links resolve (verified on
    # common-wiki-editing-setup zh_CN: all anchors present). The
    # "inconsistent references" warning is informational noise for this
    # legitimate pattern.
    g['suppress_warnings'] = ['i18n.inconsistent_references']

    # -- HTML output ---------------------------------------------------------
    g['html_theme'] = 'matrixhawk_sphinx_rtd_theme'
    g['html_theme_path'] = [sphinx_rtd_theme.get_html_theme_path()]
    # DO NOT CHANGE shorttitle semantics: the theme's Edit-on-GitHub links
    # and the /<lang>/<wiki>/ deploy layout key off it.
    g['html_short_title'] = shorttitle
    # wordmark-only variant: the tagline is illegible at sidebar size and
    # the top bar already carries it
    g['html_logo'] = '../../images/matrixhawk_logo_mark.svg'
    g['html_favicon'] = '../../images/%s' % favicon
    g['html_static_path'] = ['_static']

    # NOTE: upstream ships a plausible.ardupilot.org analytics tag here.
    # Removed for the matrixhawk deploy (rebrand debt: every page view was
    # reported to upstream's analytics). Add our own instance here if/when
    # self-hosted analytics exist.
    js = []
    if useralerts:
        js.append('./useralerts.js')
    g['html_js_files'] = js

    if copy_source is not None:
        g['html_copy_source'] = copy_source
    g['html_show_sourcelink'] = False
    g['html_show_sphinx'] = False
    g['htmlhelp_basename'] = 'ArduPilotdoc'

    # site menu base + language switcher context (theme's z_top_menu.html)
    html_context = dict(common_conf.html_context)
    html_context.update({
        'languages': common_conf.LANGUAGES,
        'url_prefix': common_conf.URL_PREFIX,
        'wiki_name': shorttitle,
        # update.py sets MWIKI_CURRENT_LANGUAGE per build_one() process so
        # the theme can render the language switcher / hreflang tags.
        'current_language': os.environ.get('MWIKI_CURRENT_LANGUAGE', 'en'),
    })
    g['html_context'] = html_context

    # --- i18n: per-vehicle locale dir + shared 'common' fallback ---
    g['locale_dirs'] = [
        '../../locale/%s/' % shorttitle,
        '../../locale/common/',
    ]
    g['gettext_compact'] = common_conf.gettext_compact
    g['gettext_uuid'] = common_conf.gettext_uuid

    # -- LaTeX / man / texinfo -----------------------------------------------
    g['latex_elements'] = {}
    g['latex_documents'] = [
        (g['master_doc'], 'ArduPilot.tex', u'ArduPilot Documentation',
         u'ArduPilot Dev Team', 'manual'),
    ]
    g['man_pages'] = [
        (g['master_doc'], 'ardupilot', u'ArduPilot Documentation',
         [g['author']], 1)
    ]
    g['texinfo_documents'] = [
        (g['master_doc'], 'ArduPilot', u'ArduPilot Documentation',
         g['author'], 'ArduPilot', 'One line description of project.',
         'Miscellaneous'),
    ]

    # -- Epub ------------------------------------------------------------------
    g['epub_title'] = project
    g['epub_author'] = g['author']
    g['epub_publisher'] = g['author']
    g['epub_copyright'] = g['copyright']
    g['epub_exclude_files'] = ['search.html']

    # Intersphinx mapping config (done globally)
    g['intersphinx_mapping'] = common_conf.intersphinx_mapping

    g['setup'] = common_conf.setup
