# -*- coding: utf-8 -*-
"""PythonAnywhere WSGI 入口"""
import sys
import os

# 项目目录
PROJECT = os.path.dirname(os.path.abspath(__file__))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from flask_app import app as application
