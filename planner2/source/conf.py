# -*- coding: utf-8 -*-
# APM Planner 2 wiki build configuration.
#
# All settings shared by the 11 wikis live in ../../vehicle_conf.py
# (single source of truth); only this wiki's identity lives here.

import sys
sys.path.insert(0, '../..')
import vehicle_conf  # noqa: E402

vehicle_conf.apply(
    globals(),
    project=u'APM Planner 2',
    shorttitle='planner2',
    favicon='favicon_default.ico',
    useralerts=False,
)
