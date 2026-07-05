# -*- coding: utf-8 -*-
# Mission Planner wiki build configuration.
#
# All settings shared by the 11 wikis live in ../../vehicle_conf.py
# (single source of truth); only this wiki's identity lives here.

import sys
sys.path.insert(0, '../..')
import vehicle_conf  # noqa: E402

vehicle_conf.apply(
    globals(),
    project=u'Mission Planner',
    shorttitle='planner',
    favicon='favicon_default.ico',
    useralerts=False,
)
