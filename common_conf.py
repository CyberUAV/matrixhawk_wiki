# This contains common configuration information for the ardupilot wikis.
# This information is imported by the conf.py files in each of the sub wikis

import os
import sys

# Add the wiki root and extensions directory to the path so our custom extensions can be found
_wiki_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _wiki_root)
sys.path.insert(0, os.path.join(_wiki_root, 'scripts', 'extensions'))

# Parallel reading of source files (use all available CPUs)
parallel_read_safe = True
parallel_write_safe = True

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.intersphinx',
    'sphinx.ext.todo',
    'sphinx.ext.mathjax',     # For :math: element rendering
    'sphinx.ext.ifconfig',
    'sphinxcontrib.youtube',  # For youtube embedding
    'sphinxcontrib.jquery',
    'sphinx_tabs.tabs',       # For clickable tabs
    'sphinx_skip_versioned_params',  # Skip labels for versioned parameter files (saves RAM/time)
    'mwiki_translation_coverage',  # Injects per-page coverage into html_context
]

# wiki_base_url='https://dl.dropboxusercontent.com/u/3067678/share2/wiki'
# intersphinx_base_url=wiki_base_url+'/%s/build/html/'

wiki_base_url = 'https://ardupilot.org/'
intersphinx_base_url = wiki_base_url + '%s/'


# html_context (incl. the language-aware 'target' menu prefix) is defined
# below, after LANGUAGES/URL_PREFIX.


# --- i18n: language code <-> URL prefix mapping (single source of truth) ---
# Sphinx internally uses standard locale codes (en, zh_CN); URLs use short
# prefixes (en, zh) for cleaner paths. update.py, conf.py and the theme
# template all read from these constants.
LANGUAGES = [
    # (sphinx_code, url_prefix, display_name)
    ('en',    'en', 'English'),
    ('zh_CN', 'zh', '简体中文'),
]
URL_PREFIX = {code: prefix for code, prefix, _ in LANGUAGES}
SPHINX_CODE_FROM_PREFIX = {prefix: code for code, prefix, _ in LANGUAGES}

# Sphinx search-indexer language per build language. Sphinx does NOT derive
# this from `language` for regional codes ('zh_CN' is not in its search
# language map, so it silently falls back to the English word splitter and
# Chinese pages become unsearchable). update.py injects this via
# confoverrides. 'zh' uses SearchChinese, which segments hanzi with jieba
# when importable (jieba is in requirements.txt).
SEARCH_LANGUAGE = {
    'en': 'en',
    'zh_CN': 'zh',
}

# Where to point the base of the build for the main site menu.
# The deployed tree is /<lang-prefix>/<wiki>/, so cross-wiki links in the
# top menu ({{target}}copter/index.html) must carry the language prefix or
# every menu click falls out of the current language tree (straight 404 on
# the bilingual site). The prefix is resolved in _set_language_target()
# below (config-inited hook), NOT here: update.py imports this module once
# at startup — before it sets MWIKI_CURRENT_LANGUAGE per build — so a
# module-level env read would be frozen at '/' for every build.
html_context = {'target': '/'}

# gettext catalog config (consumed by each vehicle's conf.py)
# Keep messages split per source file (matches sphinx-intl default layout).
gettext_compact = False
gettext_uuid = True

# Don't generate search index for versioned parameter pages
html_search_options = {
    'dict_max_word_length': 40,  # Skip very long parameter names
}

# Known wiki keys (single source of truth)
WIKI_KEYS = [
    'antennatracker',
    'ardupilot',
    'blimp',
    'copter',
    'dev',
    'mavproxy',
    'plane',
    'planner',
    'planner2',
    'rover',
    'sub',
]

# Build mapping programmatically (remote auto-discovery by using None for objects.inv)
intersphinx_mapping = {k: (intersphinx_base_url % k, None) for k in WIKI_KEYS}


# Suppress warnings that slow down builds (already have nitpicky = False)
suppress_warnings = [
    'epub.unknown_project_files',  # Suppress epub warnings
]

disable_non_local_image_warnings = True

if disable_non_local_image_warnings:
    suppress_warnings.append('image.nonlocal_uri')  # Suppress external image warnings


def _set_search_language(app, config):
    """Derive html_search_language from the (possibly overridden) build
    language. Runs at config-inited so it sees -D/confoverrides values —
    this makes zh word segmentation work for ANY builder (Read the Docs,
    plain sphinx-build), not only update.py's confoverride injection."""
    if not config.html_search_language:
        config.html_search_language = SEARCH_LANGUAGE.get(config.language, 'en')


def _set_language_target(app, config):
    """Resolve the top-menu cross-wiki prefix from the per-build language.
    Runs at config-inited (fresh env read per Sphinx app), see the
    html_context comment above for why this cannot be module-level."""
    lang = os.environ.get('MWIKI_CURRENT_LANGUAGE')
    if lang and config.html_context.get('target') == '/':
        config.html_context['target'] = '/%s/' % URL_PREFIX.get(lang, lang)


def setup(app):
    app.add_css_file("common_theme_override.css")
    app.connect('config-inited', _set_search_language)
    app.connect('config-inited', _set_language_target)
