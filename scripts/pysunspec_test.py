#!/usr/bin/env python

"""
  Copyright (c) 2014, SunSpec Alliance
  All Rights Reserved

"""

import sunspec.core.test.test_all as test

if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-t", "--raw-traceback",
        action="store_true",
        help="Print raw exception traceback for debugging")

    args = parser.parse_args()

    test.test_all(raw_traceback=args.raw_traceback)
