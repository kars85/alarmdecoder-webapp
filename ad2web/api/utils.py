# -*- coding: utf-8 -*-

from __future__ import absolute_import
import os
import base64

def generate_api_key():
    return base64.b32encode(os.urandom(7)).rstrip('==')
