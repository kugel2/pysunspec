#!/usr/bin/env python

"""
  Copyright (c) 2016, SunSpec Alliance
  All Rights Reserved

"""
from setuptools import setup, find_packages

setup(name = 'pysunspec',
      version = '1.1.0.dev2',
      description = 'Python SunSpec Tools',
      author = ['Bob Fox'],
      author_email = ['bob.fox@loggerware.com'],
      packages = find_packages(),
      package_data = {'sunspec': ['models/smdx/*'], 'sunspec.core.test': ['devices/*']},
      scripts = ['scripts/suns.py', 'scripts/pysunspec_test.py'],
      install_requires = ['pyserial', 'future']
      )
