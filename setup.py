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
      scripts = ['sunspec/scripts/suns.py'],
      entry_points={
            'console_scripts': [
                  'sunspecasync = sunspec.scripts.async:cli',
            ]
      },
      extras_require={
            ':python_version < "3.4"': ['enum34'],
            ':sys_platform == "linux2"': ['twisted', 'pyserial'],
            ':sys_platform == "linux"': ['twisted', 'pyserial'],
            ':sys_platform == "win32"': [
                  'twisted[windows_platform]',
                  # < 3 due to https://twistedmatrix.com/trac/ticket/8159
                  'pyserial<3',
            ],
            ':sys_platform == "darwin"': ['twisted[osx_platform]', 'pyserial'],
            # TODO: and for cygwin?
      },
      install_requires = [
            'future',
            'attrs',
            'click',
      ],
)
