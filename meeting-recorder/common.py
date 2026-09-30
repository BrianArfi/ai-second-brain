#!/usr/bin/env python3
"""Re-export of pipeline_common for recorder.py and the Windows GUIs.

The ASB desktop app owns this file name: it ships a trimmed common.py with the recorder, and
older app versions overwrite this file with it. That is fine, because the trimmed copy has
everything recorder.py and the GUIs import. The watcher, transcriber, ingest server and Vexa
bots import from pipeline_common.py instead, which the app never touches. Do not put new
helpers here; put them in pipeline_common.py.
"""
from pipeline_common import *  # noqa: F401,F403
from pipeline_common import detect_platform, load_config, slugify  # noqa: F401
