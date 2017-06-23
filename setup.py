#!/usr/bin/env python

"""
  Copyright (c) 2017, SunSpec Alliance
  All Rights Reserved

"""
from setuptools import setup, find_packages
setup(name = 'pysunspec',
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
            'twisted:sys_platform == "linux2"': ['twisted'],
            'twisted:sys_platform == "linux"': ['twisted'],
            'twisted:sys_platform == "win32"': ['twisted[windows_platform]'],
            'twisted:sys_platform == "darwin"': ['twisted[osx_platform]'],
            # TODO: and for cygwin?
      },
      install_requires = [
            'future',
            'attrs',
            'click',
            # Have to manually downgrade to pyserial<3 due to https://twistedmatrix.com/trac/ticket/8159
            'pyserial',
      ],
      setup_requires=['vcversioner==2.16.0.0'],
      vcversioner={
            'version_module_paths': ['sunspec/_version.py'],
            'vcs_args': ['git', '--git-dir', '%(root)s/.git', 'describe',
                         '--tags', '--long', '--abbrev=999']
      },
)
