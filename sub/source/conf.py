# -*- coding: utf-8 -*-
# Sub wiki build configuration.
#
# All settings shared by the 11 wikis live in ../../vehicle_conf.py
# (single source of truth); only this wiki's identity lives here.

import sys
sys.path.insert(0, '../..')
import vehicle_conf  # noqa: E402

vehicle_conf.apply(
    globals(),
    project=u'Sub',
    shorttitle='sub',
    favicon='favicon_sub.ico',
    useralerts=True,
    copy_source=None,
)
