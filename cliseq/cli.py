#!/usr/bin/env python
"""
Analytical pipeline for clinical DNA sequencing data

Usage:
    cliseq -h|--help
    cliseq -v|--version
    cliseq [--debug|--info] <arg>...

Options:
    -h, --help          Print help and exit
    -v, --version       Print version and exit
    --debug, --info     Execute a command with debug|info messages
"""

import logging
import os

from docopt import docopt

from . import __version__


def main():
    args = docopt(__doc__, version='v0.0.1')
    _set_log_config(debug=args['--debug'], info=args['--info'])
    logger = logging.getLogger(__name__)
    logger.debug('args:{0}{1}'.format(os.linesep, args))
    print(args)


def _set_log_config(debug=None, info=None):
    if debug:
        lv = logging.DEBUG
    elif info:
        lv = logging.INFO
    else:
        lv = logging.WARNING
    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S', level=lv
    )