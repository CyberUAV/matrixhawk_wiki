#!/usr/bin/env python3
"""

This script updates and rebuilds wiki sources from Github and from
parameters on the test server.

It is intended to be run on the main wiki server or
locally within the project's Vagrant environment.

Build notes:

* First step is always a fetch and pull from git (master).
  * Default is just a normal fetch and pull from master
  * If the --clean option is "True" then git will reset to head

* Common topics are copied from /common/source/docs.
  * Topics are copied based on information in the copywiki shortcode.
    For example a topic marked as below would only be copied to copter
     and plane wikis:
    [copywiki destination="copter,plane"]
  * Topics that don't have a [copywiki] will be copied to wikis
    the DEFAULT_COPY_WIKIS list
  * Copied topics are stripped of the 'copywiki' shortcode in the destination.
  * Copied topics are stripped of any content not marked for the target wiki
    using the "site" shortcode:
    [site wiki="plane,rover"]conditional content[/site]

Parameters files are fetched from autotest using requests

"""
from __future__ import print_function, unicode_literals

import argparse
import distutils
import errno
import filecmp
import json
import glob
import gzip
import hashlib
import multiprocessing
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import requests
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, List


from sphinx.application import Sphinx
import rst_table

from codecs import open
from datetime import datetime
# while flake8 says this is unused, distutils.dir_util.mkpath fails
# without the following import on old versions of Python:
from distutils import dir_util  # noqa: F401

from frontend.scripts import get_discourse_posts

if sys.version_info < (3, 8):
    print("Minimum python version is 3.8")
    sys.exit(1)

DEFAULT_COPY_WIKIS = ['copter', 'plane', 'rover', 'sub']
ALL_WIKIS = [
    'copter',
    'plane',
    'rover',
    'sub',
    'antennatracker',
    'dev',
    'planner',
    'planner2',
    'ardupilot',
    'mavproxy',
    'frontend',
    'blimp',
]
COMMON_DIR = 'common'

# i18n: read language <-> URL prefix mapping from common_conf so update.py,
# conf.py and the theme template share one source of truth.
import common_conf  # noqa: E402
LANGUAGES = common_conf.LANGUAGES                  # [(sphinx_code, url_prefix, name)]
URL_PREFIX = common_conf.URL_PREFIX                # {sphinx_code -> url_prefix}
DEFAULT_LANGUAGE = 'en'
COMMON_MANIFEST_PATH = 'locale/_common_manifest.json'

WIKI_NAME_TO_VEHICLE_NAME = {
    'copter': 'Copter',
    'plane': 'Plane',
    'rover': 'Rover',
    'sub': 'Sub',
    'blimp': 'Blimp',
}

# GIT_REPO = ''

PARAMETER_SITE = {
    'rover': 'APMrover2',
    'copter': 'ArduCopter',
    'plane': 'ArduPlane',
    'sub': 'ArduSub',
    'antennatracker': 'AntennaTracker',
    'AP_Periph': 'AP_Periph',
    'blimp': 'Blimp',
}
LOGMESSAGE_SITE = {
    'rover': 'Rover',
    'copter': 'Copter',
    'plane': 'Plane',
    'sub': 'Sub',
    'antennatracker': 'Tracker',
    'blimp': 'Blimp',
}
error_log = list()
N_BACKUPS_RETAIN = 10

VERBOSE = False


def debug(str_to_print):
    """Debug output if verbose is set."""
    if VERBOSE:
        print(f"[update.py]: {str_to_print}")


def progress(message, file=sys.stdout, end="\n"):
    print(f"[update.py]: {message}", file=file, end=end)


def error(str_to_print):
    """Show and count the errors."""
    global error_log
    error_log.append(str_to_print)
    print(f"[update.py][error]: {str_to_print}", file=sys.stderr)


def fatal(str_to_print):
    """Show and count the errors."""
    error(str_to_print)
    sys.exit(1)


def remove_if_exists(filepath):
    try:
        os.remove(filepath)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise e


def fetch_and_rename(fetchurl: str, target_file: str, new_name: str) -> None:
    fetch_url(fetchurl, fpath=new_name, verbose=False)
    progress(f"Renaming {new_name} to {target_file}")
    os.replace(new_name, target_file)


def fetch_url(fetchurl: str, fpath: Optional[str] = None, verbose: bool = True) -> None:
    """Fetches content at url and puts it in a file corresponding to the filename in the URL"""
    progress(f"Fetching {fetchurl}")

    if verbose:
        total_size = get_request_file_size(fetchurl)

    response = requests.get(fetchurl, stream=True)
    response.raise_for_status()

    filename = fpath or os.path.basename(urlparse(fetchurl).path)

    downloaded_size = 0
    chunk_size = 10 * 1024

    with open(filename, 'wb') as out_file:
        if verbose:
            progress("Completed : 0%", end='')
        completed_last = 0
        for chunk in response.iter_content(chunk_size=chunk_size):
            out_file.write(chunk)
            downloaded_size += len(chunk)

            # progress bar
            if verbose:
                completed = downloaded_size * 100 // total_size
                if completed - completed_last > 10 or completed == 100:
                    print(f"..{completed}%", end='')
                    completed_last = completed
        if verbose:
            print()  # Newline to correct the console cursor position


def get_request_file_size(url: str) -> int:
    headers = {'Accept-Encoding': 'identity'}  # needed as request use compression by default
    hresponse = requests.head(url, headers=headers)

    if 'Content-Length' in hresponse.headers:
        size = int(hresponse.headers['Content-Length'])
        return size
    return 0


def fetchparameters(site: Optional[str] = None, cache: Optional[str] = None) -> None:
    dataname = "Parameters"
    fetch_ardupilot_generated_data(PARAMETER_SITE, f'https://autotest.ardupilot.org/{dataname}', f'{dataname}.rst',
                                   f'{dataname.lower()}.rst', site, cache)


def fetchlogmessages(site: Optional[str] = None, cache: Optional[str] = None) -> None:
    dataname = "LogMessages"
    fetch_ardupilot_generated_data(LOGMESSAGE_SITE, f'https://autotest.ardupilot.org/{dataname}', f'{dataname}.rst',
                                   f'{dataname.lower()}.rst', site, cache)


def fetch_ardupilot_generated_data(site_mapping: Dict, base_url: str, sub_url: str, document_name: str,
                                   site: Optional[str] = None, cache: Optional[str] = None) -> None:
    """Fetches the data for all the sites from the test server and
    copies them to the correct location.

    This is always run as part of a build (i.e. no checking to see if
    parameters or logmessage have changed.)

    """
    urls: List[str] = []
    targetfiles: List[str] = []
    names: List[str] = []

    for key, value in site_mapping.items():
        fetchurl = f'{base_url}/{value}/{sub_url}'
        targetfile = f'./{key}/source/docs/{document_name}'
        if key == 'AP_Periph':
            targetfile = f'./dev/source/docs/AP_Periph-{sub_url}'
        if cache:
            if not os.path.exists(targetfile):
                raise Exception(f"Asked to use cached files, but {targetfile} does not exist")
            continue
        if site == key or site is None or (site == 'dev' and key == 'AP_Periph'):
            urls.append(fetchurl)
            targetfiles.append(targetfile)
            names.append(f"{value}_{document_name}")

    with ThreadPoolExecutor() as executor:
        executor.map(fetch_and_rename, urls, targetfiles, names, timeout=5*60)


def build_one(wiki, lang, fast):
    """build one wiki in a single language.

    Each (wiki, lang) pair writes to its own html-<lang>/ and doctrees-<lang>/
    so concurrent languages do not collide and a non-fast rebuild only wipes
    the current language's artifacts.
    """
    progress(f'build_one: {wiki} [{lang}]')

    source_dir = os.path.join(wiki, 'source')
    output_dir = os.path.join(wiki, 'build')
    html_dir = os.path.join(output_dir, f'html-{lang}')
    doctree_dir = os.path.join(output_dir, f'doctrees-{lang}')

    if not fast:
        if os.path.exists(html_dir):
            shutil.rmtree(html_dir)
        if os.path.exists(doctree_dir):
            shutil.rmtree(doctree_dir)

    # Child-local env var, read by conf.py to populate html_context.current_language.
    # Safe under multiprocessing because each build_one runs in its own process.
    os.environ['MWIKI_CURRENT_LANGUAGE'] = lang

    app = Sphinx(
        buildername='html',
        confdir=source_dir,
        doctreedir=doctree_dir,
        outdir=html_dir,
        parallel=2,
        srcdir=source_dir,
        confoverrides={
            'language': lang,
            # zh_CN must map to the 'zh' search indexer (jieba word
            # segmentation) or Chinese pages are not searchable; see
            # common_conf.SEARCH_LANGUAGE.
            'html_search_language': common_conf.SEARCH_LANGUAGE.get(lang, 'en'),
        },
    )
    app.build()


def sphinx_make(site, parallel, fast, languages):
    """
    Build the cartesian product of (vehicle wiki) x (language) in parallel.
    `languages` is a list of Sphinx locale codes (e.g. ['en', 'zh_CN']).
    """
    jobs = []
    for wiki in ALL_WIKIS:
        if site == 'common' or site == 'frontend':
            continue
        if wiki == 'frontend':
            continue
        if site is not None and site != wiki:
            continue
        for lang in languages:
            jobs.append((wiki, lang))

    procs = []
    for wiki, lang in jobs:
        p = multiprocessing.Process(target=build_one, args=(wiki, lang, fast))
        p.start()
        procs.append(p)
        while parallel != -1 and len(procs) >= parallel:
            for p in procs:
                if p.exitcode is not None:
                    p.join()
                    procs.remove(p)
                    if p.exitcode != 0:
                        error('Error making sphinx(1)')
            time.sleep(0.1)
    while len(procs) > 0:
        for p in procs[:]:
            if p.exitcode is not None:
                p.join()
                procs.remove(p)
                if p.exitcode != 0:
                    error('Error making sphinx(2)')
        time.sleep(0.1)


def check_build(site, languages):
    """
    check that build was successful for each (wiki, language) pair
    """
    if platform.system() == "Windows":
        debug("Skipping check_build on windows")
        return
    for wiki in ALL_WIKIS:
        if site is not None and site != wiki:
            continue
        if wiki in ['common', 'frontend']:
            continue
        for lang in languages:
            index_html = os.path.join(wiki, "build", f"html-{lang}", "index.html")
            if not os.path.exists(index_html):
                fatal("%s [%s] site not built - missing %s" % (wiki, lang, index_html))


def copy_build(site, destdir, languages):
    """
    Copies each (wiki, lang) build into <destdir>/<url_prefix>/<wiki>/.
    URL_PREFIX maps sphinx locale codes (e.g. 'zh_CN') to short URL segments
    (e.g. 'zh') so the deployed tree is /en/copter/, /zh/copter/, ...
    """
    for wiki in ALL_WIKIS:
        if site == 'common':
            continue
        if site is not None and site != wiki:
            continue
        if wiki == 'frontend':
            continue
        for lang in languages:
            prefix = URL_PREFIX.get(lang, lang)
            debug('Copy: %s [%s -> %s]' % (wiki, lang, prefix))
            lang_root = os.path.join(destdir, prefix)
            os.makedirs(lang_root, exist_ok=True)
            targetdir = os.path.join(lang_root, wiki)

            olddir = os.path.join(destdir, f'old-{prefix}-{wiki}')
            if os.path.exists(olddir):
                shutil.rmtree(olddir)
            if os.path.exists(targetdir):
                debug('Moving %s into %s' % (targetdir, olddir))
                shutil.move(targetdir, olddir)

            sourcedir = './%s/build/html-%s/' % (wiki, lang)
            try:
                shutil.move(sourcedir, targetdir)
                debug(f"Moved to {targetdir}")
            except shutil.Error:
                error(f"FAIL moving output to {targetdir}")

            os.makedirs(os.path.join(targetdir, '_static'), exist_ok=True)

            if os.path.exists(olddir):
                debug('Removing %s' % olddir)
                shutil.rmtree(olddir)


def make_backup(site, destdir, backupdestdir, languages):
    """
    backup current site (per language)
    """
    for wiki in ALL_WIKIS:
        if site == 'common':
            continue
        if site is not None and site != wiki:
            continue
        if wiki == 'frontend':
            continue
        for lang in languages:
            prefix = URL_PREFIX.get(lang, lang)
            debug('Backing up: %s [%s]' % (wiki, prefix))

            targetdir = os.path.join(destdir, prefix, wiki)
            distutils.dir_util.mkpath(targetdir)

            if not os.path.exists(targetdir):
                fatal("FAIL backup when looking for folder %s" % targetdir)

            bkdir = os.path.join(backupdestdir, str(building_time + '-wiki-bkp'), prefix, str(wiki))
            debug('Checking %s' % bkdir)
            distutils.dir_util.mkpath(bkdir)
            debug('Copying %s into %s' % (targetdir, bkdir))
            try:
                subprocess.check_call(["rsync", "-a", "--delete", targetdir + "/", bkdir])
            except subprocess.CalledProcessError as ex:
                progress(ex)
                fatal("Failed to backup %s [%s]" % (wiki, prefix))


def delete_old_wiki_backups(folder, n_to_keep):
    try:
        debug('Checking number of backups in folder %s' % folder)
        backup_folders = glob.glob(folder + "/*-wiki-bkp/")
        backup_folders.sort()
        if len(backup_folders) > n_to_keep:
            for i in range(0, len(backup_folders) - n_to_keep):
                if '-wiki-bkp' in str(backup_folders[i]):
                    debug('Deleting folder %s' % str(backup_folders[i]))
                    shutil.rmtree(str(backup_folders[i]))
                else:
                    debug('Ignoring folder %s because it does not look like a auto generated wiki backup folder' %
                          str(backup_folders[i]))
        else:
            debug('No old backups to delete in %s' % folder)
    except Exception as e:
        error('Error on deleting some previous wiki backup folders: %s' % e)


def create_dir_if_not_exists(dir_path: str) -> None:
    try:
        os.mkdir(dir_path)
    except FileExistsError:  # Catching specific exception
        pass


def copy_common_source_files(start_dir=COMMON_DIR):
    """
    copies files common to all Wikis to the source directories for each Wiki.

    Also writes locale/_common_manifest.json mapping each vehicle to the list
    of docnames it received from common/. i18n_sync_common.py reads this to
    deduplicate translations of common-sourced pages.
    """

    # Clean existing common topics (easiest way to guarantee old ones
    # are removed)
    # Cost is that these will have to be rebuilt even if not changed
    import glob
    for wiki in ALL_WIKIS:
        files = glob.glob('%s/source/docs/common-*.rst' % wiki)
        for f in files:
            debug('Remove existing common: %s' % f)
            os.remove(f)

    # Create destination folders that might be needed (if don't exist)
    for wiki in ALL_WIKIS:
        create_dir_if_not_exists(wiki)
        create_dir_if_not_exists(f'{wiki}/source')
        create_dir_if_not_exists(f'{wiki}/source/docs')
        create_dir_if_not_exists(f'{wiki}/source/_static')

    manifest: Dict[str, List[str]] = {}

    debug("Copying common source files to each Wiki")
    for root, dirs, files in os.walk(start_dir):
        for file in files:
            if file.endswith(".rst"):
                debug("  FILE: %s" % file)
                source_file_path = os.path.join(root, file)
                source_file = open(source_file_path, 'r', 'utf-8')
                source_content = source_file.read()
                source_file.close()
                targets = get_copy_targets(source_content)
                # progress(targets)
                docname = f"docs/{os.path.splitext(file)[0]}"
                for wiki in targets:
                    # progress("CopyTarget: %s" % wiki)
                    content = strip_content(source_content, wiki)
                    targetfile = '%s/source/docs/%s' % (wiki, file)
                    debug(f"    {targetfile}")
                    destination_file = open(targetfile, 'w', 'utf-8')
                    destination_file.write(content)
                    destination_file.close()
                    manifest.setdefault(wiki, []).append(docname)
            elif file.endswith(".css"):
                for wiki in ALL_WIKIS:
                    shutil.copy2(os.path.join(root, file),
                                 '%s/source/_static/' % wiki)
            elif file.endswith(".js"):
                source_file_path = os.path.join(root, file)
                source_file = open(source_file_path, 'r', 'utf-8')
                source_content = source_file.read()
                source_file.close()
                targets = get_copy_targets(source_content)
                # progress("JS: " + str(targets))
                for wiki in targets:
                    content = strip_content(source_content, wiki)
                    targetfile = '%s/source/_static/%s' % (wiki, file)
                    debug(f"    {targetfile}")
                    destination_file = open(targetfile, 'w', 'utf-8')
                    destination_file.write(content)
                    destination_file.close()

    # Deduplicate (multiple roots could produce same docname) and persist
    # manifest so scripts/i18n_sync_common.py can move common-sourced .po
    # entries into the shared locale/common/ catalog.
    for wiki in manifest:
        manifest[wiki] = sorted(set(manifest[wiki]))
    try:
        os.makedirs(os.path.dirname(COMMON_MANIFEST_PATH), exist_ok=True)
        with open(COMMON_MANIFEST_PATH, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        debug(f'wrote {COMMON_MANIFEST_PATH} ({sum(len(v) for v in manifest.values())} entries)')
    except Exception as e:
        error(f'failed to write {COMMON_MANIFEST_PATH}: {e}')


def get_copy_targets(content):
    p = re.compile(r'\[copywiki.*?destination\=\"(.*?)\".*?\]', flags=re.S)
    m = p.search(content)
    targetset = set()
    if m:
        targets = m.group(1).split(',')
        for item in targets:
            targetset.add(item.strip())
    else:
        targetset = set(DEFAULT_COPY_WIKIS)
    return targetset


def strip_content(content, site):
    """
    Strips the copywiki shortcode. Removes content for other sites and
    the [site] shortcode itself.
    """

    def fix_copywiki_shortcode(matchobj):
        """
        Strip the copywiki shortcode if found (just return "nothing" to
        result of re)
        """
        # logmatch_code(matchobj, 'STRIP')
        # progress("STRIPPED")
        return ''

    # Remove the copywiki from content
    newText = re.sub(r'\[copywiki.*?\]',
                     fix_copywiki_shortcode,
                     content,
                     flags=re.M)

    def fix_site_shortcode(matchobj):
        # logmatch_code(matchobj, 'SITESC_')
        sitelist = matchobj.group(1)
        # progress("SITES_BLOCK: %s" % sitelist)
        if site not in sitelist:
            # progress("NOT")
            return ''
        else:
            # progress("YES")
            return matchobj.group(2)
    # Remove the site shortcode from content
    newText = re.sub(r'\[site\s.*?wiki\=\"(.*?)\".*?\](.*?)\[\/site\]',
                     fix_site_shortcode,
                     newText,
                     flags=re.S)

    return newText


def logmatch_code(matchobj, prefix):

    for i in range(9):
        try:
            progress("%s m%d: %s" % (prefix, i, matchobj.group(i)))
        except IndexError:  # The object has less groups than expected
            progress("%s: except m%d" % (prefix, i))


def is_the_same_file(file1, file2):
    """ Compare two files using their SHA256 hashes"""
    digests = []
    for filename in [file1, file2]:
        hasher = hashlib.sha256()
        with open(filename, 'rb') as f:
            buf = f.read()
            hasher.update(buf)
            a = hasher.hexdigest()
            digests.append(a)

    return digests[0] == digests[1]


def fetch_versioned_parameters(site=None):
    """
    It relies on "build_parameters.py" be executed before the "update.py"

    Once the generated files are on ../new_params_mversion it tut all
    parameters and JSON files in their destinations.
    """

    for key, value in PARAMETER_SITE.items():

        if key == 'AP_Periph': # workaround until create a versioning for AP_Periph in firmware server
            fetchurl = 'https://autotest.ardupilot.org/Parameters/%s/Parameters.rst' % value
            targetfile = './dev/source/docs/AP_Periph-Parameters.rst'
            fetch_and_rename(fetchurl, targetfile, 'Parameters.rst')

        else: # regular versining

            if site == key or site is None:
                # Remove old param single file
                single_param_file = './%s/source/docs/parameters.rst' % key
                debug("Erasing " + single_param_file)
                remove_if_exists(single_param_file)

                # Remove old versioned param files
                if 'antennatracker' in key.lower():  # To main the original script approach instead of the build_parameters.py approach.  # noqa: E501
                    old_parameters_mask = (os.getcwd() +
                                           '/%s/source/docs/parameters-%s-' %
                                           ("AntennaTracker", "AntennaTracker"))
                else:
                    old_parameters_mask = (os.getcwd() +
                                           '/%s/source/docs/parameters-%s-' %
                                           (key, key.title()))
                try:
                    old_parameters_files = [
                        f for f in glob.glob(old_parameters_mask + "*.rst")]
                    for filename in old_parameters_files:
                        debug("Erasing rst " + filename)
                        os.remove(filename)
                except Exception as e:
                    error(e)
                    pass

                # Remove old json file
                if 'antennatracker' in key.lower():  # To main the original script approach instead of the build_parameters.py approach.  # noqa: E501
                    target_json_file = ('./%s/source/_static/parameters-%s.json' %
                                        ("AntennaTracker", "AntennaTracker"))
                else:
                    target_json_file = ('./%s/source/_static/parameters-%s.json' %
                                        (value, key.title()))
                debug("Erasing json " + target_json_file)
                remove_if_exists(target_json_file)

                # Moves the updated JSON file
                if 'antennatracker' in key.lower():  # To main the original script approach instead of the build_parameters.py approach.  # noqa: E501
                    vehicle_json_file = os.getcwd() + '/../new_params_mversion/%s/parameters-%s.json' % ("AntennaTracker", "AntennaTracker")  # noqa: E501
                else:
                    vehicle_json_file = os.getcwd() + '/../new_params_mversion/%s/parameters-%s.json' % (value, key.title())
                new_file = (
                    key +
                    "/source/_static/" +
                    vehicle_json_file[str(vehicle_json_file).rfind("/")+1:])
                try:
                    debug("Moving " + vehicle_json_file)
                    # os.rename(vehicle_json_file, new_file)
                    shutil.copy2(vehicle_json_file, new_file)
                except Exception as e:
                    error(e)
                    pass

                # Copy all parameter files to vehicle folder IFF it is new
                try:
                    new_parameters_folder = (os.getcwd() +
                                             '/../new_params_mversion/%s/' % value)
                    new_parameters_files = [
                        f for f in glob.glob(new_parameters_folder + "*.rst")
                    ]
                except Exception as e:
                    error(e)
                    pass
                for filename in new_parameters_files:
                    # Check possible cached version
                    try:
                        new_file = (key +
                                    "/source/docs/" +
                                    filename[str(filename).rfind("/")+1:])
                        if not os.path.isfile(new_file):
                            debug("Copying %s to %s (target file does not exist)" % (filename, new_file))
                            shutil.copy2(filename, new_file)
                        elif os.path.isfile(filename.replace("new_params_mversion", "old_params_mversion")): # The cached file exists?  # noqa: E501

                            # Temporary debug messages to help with cache tasks.
                            debug("Check cache: %s against %s" % (filename, filename.replace("new_params_mversion", "old_params_mversion")))  # noqa: E501
                            debug("Check cache with filecmp.cmp: %s" % filecmp.cmp(filename, filename.replace("new_params_mversion", "old_params_mversion")))  # noqa: E501
                            debug("Check cache with sha256: %s" % is_the_same_file(filename, filename.replace("new_params_mversion", "old_params_mversion")))  # noqa: E501

                            if ("parameters.rst" in filename) or (not filecmp.cmp(filename, filename.replace("new_params_mversion", "old_params_mversion"))):    # It is different?  OR is this one the latest. | Latest file must be built everytime in order to enable Sphinx create the correct references across the wiki.  # noqa: E501
                                debug("Overwriting %s to %s" % (filename, new_file))
                                shutil.copy2(filename, new_file)
                            else:
                                debug("It will reuse the last build of " + new_file)
                        else:   # If not cached, copy it anyway.
                            debug("Copying %s to %s" % (filename, new_file))
                            shutil.copy2(filename, new_file)

                    except Exception as e:
                        error(e)
                        pass


def create_latest_parameter_redirect(default_param_file, vehicle):
    """
    For a given vehicle create a file called parameters.rst that
    redirects to the latest parameters file.(Create to maintaim retro
    compatibility.)
    """
    out_line = "======================\nParameters List (Full)(\n======================\n"
    out_line += "\n.. raw:: html\n\n"
    out_line += "   <script>location.replace(\"" + default_param_file[:-3] + "html" + "\")</script>"
    out_line += "\n\n"

    filename = vehicle + "/source/docs/parameters.rst"
    with open(filename, "w") as text_file:
        text_file.write(out_line)

    debug("Created html automatic redirection from parameters.html to %shtml" %
          default_param_file[:-3])


def cache_parameters_files(site=None, languages=None):
    """
    For each vechile: put new_params_mversion/ content in
    old_params_mversion/ folders and .html built files as well.

    Parameters HTML content is the same across languages (just data tables),
    so we cache from the first available language build.
    """
    langs = languages or [DEFAULT_LANGUAGE]
    for key, value in PARAMETER_SITE.items():
        if (site == key or site is None) and (key != 'AP_Periph'):  # and (key != 'AP_Periph') workaround until create a versioning for AP_Periph in firmware server # noqa: E501
            try:
                old_parameters_folder = (os.getcwd() +
                                         '/../old_params_mversion/%s/' % value)
                old_parameters_files = [
                    f for f in glob.glob(old_parameters_folder + "*.*")
                ]
                for file in old_parameters_files:
                    debug("Removing %s" % file)
                    os.remove(file)

                new_parameters_folder = (os.getcwd() +
                                         '/../new_params_mversion/%s/' % value)
                new_parameters_files = [
                    f for f in glob.glob(new_parameters_folder +
                                         "parameters-*.rst")
                ]
                for filename in new_parameters_files:
                    debug("Copying %s to %s" %
                          (filename, old_parameters_folder))
                    shutil.copy2(filename, old_parameters_folder)

                for lang in langs:
                    built_folder = os.getcwd() + "/" + key + f"/build/html-{lang}/docs/"
                    if not os.path.isdir(built_folder):
                        continue
                    built_parameters_files = [
                        f for f in glob.glob(built_folder + "parameters-*.html")
                    ]
                    for built in built_parameters_files:
                        debug("Copying %s to %s" %
                              (built, old_parameters_folder))
                        shutil.copy2(built, old_parameters_folder)
                    break  # one language is enough

            except Exception as e:
                error(e)
                pass


def put_cached_parameters_files_in_sites(site=None, languages=None):
    """
    For each vechile: put built .html files in every language's site folder.
    """
    langs = languages or [DEFAULT_LANGUAGE]
    for key, value in PARAMETER_SITE.items():
        if (site == key or site is None) and (key != 'AP_Periph'): # and (key != 'AP_Periph') workaround until create a versioning for AP_Periph in firmware server # noqa: E501
            try:
                built_folder = (os.getcwd() +
                                '/../old_params_mversion/%s/' % value)
                built_parameters_files = [
                    f for f in glob.glob(built_folder + "parameters-*.html")
                ]
                for lang in langs:
                    vehicle_folder = os.getcwd() + "/" + key + f"/build/html-{lang}/docs/"
                    if not os.path.isdir(vehicle_folder):
                        continue
                    debug("Site %s [%s] getting previously built files from %s" %
                          (site, lang, built_folder))
                    for built in built_parameters_files:
                        if ("latest" not in built):  # latest parameters files must be built every time
                            debug("Reusing built %s in %s " %
                                  (built, vehicle_folder))
                            shutil.copy(built, vehicle_folder)
            except Exception as e:
                error(e)
                pass


def update_frontend_json():
    """
    Frontend get posts from Forum server and insert it into JSON
    """
    debug('Running script to get last posts from forum server.')
    try:
        get_discourse_posts.main()
    except Exception as e:
        error(e)
        pass


def copy_static_html_sites(site, destdir, languages=None):
    """
    Copy the static frontend landing site into each language root so that
    /en/ and /zh/ each have a working landing page that links to the wiki
    under the same language prefix (frontend/index.html uses relative
    ./<vehicle>/ links, which resolve correctly within /<lang>/).

    Also writes <destdir>/index.html as a language-redirect entry page so
    bare matrixhawk.hk lands somewhere sensible.

    Phase 2A: same (English) frontend is replicated into every language.
    Phase 2B will replace this with real per-language frontend authoring.
    """
    if not (site in ['frontend', None] and destdir is not None):
        return

    debug('Copying static sites (only frontend so far).')
    update_frontend_json()
    langs = languages or [DEFAULT_LANGUAGE]
    folder = 'frontend'
    site_folder = os.path.join(os.getcwd(), folder)

    for lang in langs:
        prefix = URL_PREFIX.get(lang, lang)
        lang_root = os.path.join(destdir, prefix)
        try:
            os.makedirs(lang_root, exist_ok=True)
            # Copy each file/subdir from frontend/ into <destdir>/<prefix>/.
            # Skip per-language index variants — they're applied below as overrides.
            for entry in os.listdir(site_folder):
                if entry.startswith('index.') and entry != 'index.html' and entry.endswith('.html'):
                    continue
                src = os.path.join(site_folder, entry)
                dst = os.path.join(lang_root, entry)
                if os.path.exists(dst):
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    else:
                        os.remove(dst)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            # If a per-language frontend variant exists, use it as index.html.
            # Convention: frontend/index.<sphinx_code>.html (e.g. index.zh_CN.html)
            variant = os.path.join(site_folder, f'index.{lang}.html')
            if os.path.exists(variant):
                shutil.copy2(variant, os.path.join(lang_root, 'index.html'))
                debug(f'frontend: using {variant} as {lang_root}/index.html')
            debug(f'frontend copied into {lang_root}')
        except Exception as e:
            error(e)

    _write_top_level_redirect(destdir, langs)


def _write_top_level_redirect(destdir, languages):
    """Write <destdir>/index.html that redirects to /<default_lang>/, with a
    JS Accept-Language sniff so zh-* browsers land on /zh/."""
    default_prefix = URL_PREFIX.get(DEFAULT_LANGUAGE, 'en')
    # Build list of (prefix, sphinx_code) for the JS sniff
    options = [(URL_PREFIX[c], c) for c, _, _ in LANGUAGES if c in URL_PREFIX]
    js_options = ", ".join(f'["{p}", "{c}"]' for p, c in options)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MatrixHawk Wiki</title>
<script>
(function() {{
  var langs = [{js_options}];
  var nav = (navigator.language || navigator.userLanguage || "en").toLowerCase();
  for (var i = 0; i < langs.length; i++) {{
    var prefix = langs[i][0], code = langs[i][1].toLowerCase();
    if (nav.startsWith(code) || nav.startsWith(code.split("_")[0])) {{
      location.replace("/" + prefix + "/");
      return;
    }}
  }}
  location.replace("/{default_prefix}/");
}})();
</script>
<meta http-equiv="refresh" content="0;url=/{default_prefix}/">
</head>
<body>
<p>Redirecting to <a href="/{default_prefix}/">/{default_prefix}/</a> ...</p>
</body>
</html>
"""
    path = os.path.join(destdir, 'index.html')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        debug(f'wrote top-level redirect {path}')
    except Exception as e:
        error(e)


def check_imports():
    '''check key imports work'''
    import importlib.metadata
    # package names to check the versions of. Note that these can be different than the string used to import the package
    required_packages = ["matrixhawk_sphinx_rtd_theme>=0.1.0", "sphinxcontrib.youtube>=1.2.0", "sphinx>=7.1.2", "docutils<0.19"]
    for package in required_packages:
        debug("Checking for %s" % package)
        try:
            importlib.metadata.version(package.split("<")[0].split(">=")[0])
        except importlib.metadata.PackageNotFoundError as ex:
            progress(ex)
            fatal("Require %s\nPlease run the wiki build setup script \"Sphinxsetup\"" % package)
    debug("Imports OK")


def check_ref_directives():
    '''check formatting around ref directive that sphinx does not warn about'''
    character_before_ref_tag = re.compile(r"[a-zA-Z0-9_:]:ref:")
    character_after_ref_tag = re.compile(r"(:ref:`.*?`[_]{0,2}) ([\.,:])")

    # don't check "common="" files in vehicle wikis
    skipped_files = set()
    for wiki in ALL_WIKIS:
        skipped_files.update(glob.glob(f'{wiki}/source/docs/common-*.rst'))
    wiki_glob = set(glob.glob("**/*.rst", recursive=True))
    files_to_check = wiki_glob.difference(skipped_files)
    for f in files_to_check:
        with open(f, "r", "utf-8") as file:
            try:
                for i, line in enumerate(file.readlines()):
                    if character_before_ref_tag.search(line):
                        error(f"Remove character before ref directive in \"{f}\" on line number {i+1}")
                    if character_after_ref_tag.search(line):
                        error(f"Remove character after ref directive in \"{f}\" on line number {i+1}")
            except UnicodeDecodeError as ex:
                print("UnicodeError in %s: " % f, ex)
                sys.exit(1)


def create_features_pages(site):
    '''for each vehicle, write out a page containing features for each
    supported board'''

    debug("Creating features pages")

    # grab build_options which allows us to map from define to name
    # and description.  Create a convenience hash for it
    remove_if_exists("build_options.py")
    fetch_url("https://raw.githubusercontent.com/ArduPilot/ardupilot/master/Tools/scripts/build_options.py")
    import build_options
    build_options_by_define = {}
    for f in build_options.BUILD_OPTIONS:
        build_options_by_define[f.define] = f

    # fetch and load most-recently-built features.json
    remove_if_exists("features.json.gz")
    fetch_url("https://firmware.ardupilot.org/features.json.gz")
    features_json = json.load(gzip.open("features.json.gz"))
    if features_json["format-version"] != "1.0.0":
        progress("bad format version")
        return
    features = features_json["features"]

    # progress("features: (%s)" % str(features))
    for wiki in WIKI_NAME_TO_VEHICLE_NAME.keys():
        debug(wiki)
        if site is not None and site != wiki:
            continue
        if wiki not in WIKI_NAME_TO_VEHICLE_NAME:
            continue
        vehicletype = WIKI_NAME_TO_VEHICLE_NAME[wiki]
        content = create_features_page(features, build_options_by_define, vehicletype)
        if wiki == "AP_Periph":
            destination_filepath = "dev/source/docs/periph-binary-features.rst"
        else:
            destination_filepath = "%s/source/docs/binary-features.rst" % wiki
        # make .../docs/ directory if it doesn't already exist
        os.makedirs(os.path.dirname(destination_filepath), exist_ok=True)
        with open(destination_filepath, "w") as f:
            f.write(content)


def reference_for_board(board):
    '''return a string suitable for creating an anchor in RST to make
    board's feture table linkable'''
    return "FEATURE_%s" % board


def create_features_page(features, build_options_by_define, vehicletype):
    features_by_platform = {}
    for build in features:
        # progress("build: (%s)" % str(build))
        if build["vehicletype"] != vehicletype:
            continue
        features_by_platform[build["platform"]] = build["features"]
    rows = []
    column_headings = ["Category", "Feature", "Included", "Description"]
    all_tables = ""
    for platform_key in sorted(features_by_platform.keys(), key=lambda x : x.lower()):
        rows = []
        platform_features = features_by_platform[platform_key]
        sorted_platform_features_in = []
        sorted_platform_features_not_in = []
        features_in = {}
        for feature in platform_features:
            feature_in = not feature.startswith("!")
            if not feature_in:
                feature = feature[1:]
            features_in[feature] = feature_in
            try:
                build_options = build_options_by_define[feature]
            except KeyError:
                # mismatch between build_options.py and features.json
                progress("feature %s (%s,%s) not in build_options.py" %
                         (feature, platform_key, vehicletype))
                continue
            if feature_in:
                some_list = sorted_platform_features_in
            else:
                some_list = sorted_platform_features_not_in
            some_list.append((build_options.category, feature))

        sorted_platform_features = (
            sorted(sorted_platform_features_not_in, key=lambda x : x[0] + x[1]) +
            sorted(sorted_platform_features_in, key=lambda x : x[0] + x[1]))

        for (category, feature) in sorted_platform_features:
            build_options = build_options_by_define[feature]
            row = [category, build_options.label]
            if features_in[feature]:
                row.append("Yes")
            else:
                row.append("No")
            row.append(build_options.description)
            if not features_in[feature]:
                # for now, do not include features that are on the
                # board, just those that aren't, per Henry's request:
                rows.append(row)
        if len(rows) == 0:
            t = ""
        else:
            t = rst_table.tablify(rows, headings=column_headings)
        underline = "-" * len(platform_key)
        all_tables += ('''
.. _%s:

%s
%s

%s
''' % (reference_for_board(platform_key), platform_key, underline, t))

    index = ""
    for board in sorted(features_by_platform.keys(), key=lambda x : x.lower()):
        index += '- :ref:`%s<%s>`\n\n' % (board, reference_for_board(board))

    all_features_rows = []
    for feature in sorted(build_options_by_define.values(), key=lambda x : (x.category + x.label).lower()):
        all_features_rows.append([feature.category, feature.label, feature.description])
    all_features = rst_table.tablify(all_features_rows, headings=["Category", "Feature", "Description"])

    return '''
.. _binary-features:

=====================================
List of Firmware Limitations by Board
=====================================

**Dynamically generated by update.py.  Do not edit.**

%s Omitted features by board type in "latest" builds from build server


Board Index
===========

%s

.. _all-features:

All Features
============

%s

Boards
======

%s
''' % (vehicletype, index, all_features, all_tables)

#######################################################################


if __name__ == "__main__":

    if platform.system() == "Windows":
        multiprocessing.freeze_support()

    # Set up option parsing to get connection string
    parser = argparse.ArgumentParser(
        description='Copy Common Files as needed, stripping out non-relevant wiki content',
    )
    parser.add_argument(
        '--site',
        help="If you just want to copy to one site, you can do this. Otherwise will be copied.",
    )
    parser.add_argument(
        '--clean',
        action='store_true',
        help="Does a very clean build - resets git to master head (and TBD cleans up any duplicates in the output).",
    )
    parser.add_argument(
        '--cached-parameter-files',
        action='store_true',
        help="Do not re-download parameter files",
    )
    parser.add_argument(
        '--parallel',
        type=int,
        help="limit parallel builds, -1 for unlimited",
        default=1,
    )
    parser.add_argument(
        '--destdir',
        default=None,
        help="Destination directory for compiled docs",
    )
    parser.add_argument(
        '--enablebackups',
        action='store_true',
        default=False,
        help="Enable several backups up to const N_BACKUPS_RETAIN in --backupdestdir folder",
    )
    parser.add_argument(
        '--backupdestdir',
        default="/var/sites/wiki-backup/web",
        help="Destination directory for compiled docs",
    )
    parser.add_argument(
        '--paramversioning',
        action='store_true',
        default=False,
        help="Build multiple parameters pages for each vehicle based on its firmware repo.",
    )
    parser.add_argument(
        '--verbose',
        dest='verbose',
        action='store_true',
        default=False,
        help="show debugging output",
    )
    parser.add_argument(
        '--fast',
        dest='fast',
        action='store_true',
        default=False,
        help=("Incremental build using already downloaded parameters, log messages, and video thumbnails rather than cleaning "
              "before build."),
    )
    parser.add_argument(
        '--languages',
        default=DEFAULT_LANGUAGE,
        help=("Comma-separated list of Sphinx language codes to build, e.g. "
              "'en,zh_CN'. Output goes to <destdir>/<url_prefix>/<vehicle>/ "
              "using the URL_PREFIX map in common_conf.py."),
    )

    args = parser.parse_args()
    # Resolve --languages into a clean list and verify each is configured.
    languages = [code.strip() for code in args.languages.split(',') if code.strip()]
    unknown = [c for c in languages if c not in URL_PREFIX]
    if unknown:
        fatal(f"Unknown language code(s): {unknown}. Known: {list(URL_PREFIX)}. "
              f"Add them to LANGUAGES in common_conf.py first.")
    # progress(args.site)
    # progress(args.clean)

    VERBOSE = args.verbose

    now = datetime.now()
    building_time = now.strftime("%Y-%m-%d-%H-%M-%S")

    check_imports()
    check_ref_directives()
    create_features_pages(args.site)

    if not args.fast:
        if args.paramversioning:
            # Parameters for all versions availble on firmware.ardupilot.org:
            fetch_versioned_parameters(args.site)
        else:
            # Single parameters file. Just present the latest parameters:
            fetchparameters(args.site, args.cached_parameter_files)

        # Fetch most recent LogMessage metadata from autotest:
        fetchlogmessages(args.site, args.cached_parameter_files)

    copy_static_html_sites(args.site, args.destdir, languages)
    copy_common_source_files()
    sphinx_make(args.site, args.parallel, args.fast, languages)

    if args.paramversioning:
        put_cached_parameters_files_in_sites(args.site, languages)
        cache_parameters_files(args.site, languages)

    check_build(args.site, languages)

    if args.enablebackups:
        make_backup(args.site, args.destdir, args.backupdestdir, languages)
        delete_old_wiki_backups(args.backupdestdir, N_BACKUPS_RETAIN)

    if args.destdir:
        copy_build(args.site, args.destdir, languages)

    # To navigate locally and view versioning script for parameters
    # working is necessary run Chrome as "chrome
    # --allow-file-access-from-files". Otherwise it will appear empty
    # locally and working once is on the server.

    error_count = len(error_log)
    if error_count > 0:
        progress("Reprinting error messages:", file=sys.stderr)
        for msg in error_log:
            print(f"\033[1;31m[update.py][error]: {msg}\033[0m", file=sys.stderr)
        fatal(f"{error_count} errors during Wiki build")
    else:
        print("Build completed without errors")

    sys.exit(0)
