#!/usr/bin/env python

"""
  Copyright (c) 2017, SunSpec Alliance
  All Rights Reserved

"""
from setuptools import setup, find_packages

setup(name = 'pysunspec',
      version = '1.1.0.dev3',
      description = 'Python SunSpec Tools',
      author = ['Bob Fox'],
      author_email = ['bob.fox@loggerware.com'],
      packages = find_packages(),
      package_data = {'sunspec': ['models/smdx/*'], 'sunspec.core.test': ['devices/*']},
      scripts = ['scripts/suns.py'],
      install_requires = ['pyserial', 'future']
      )
