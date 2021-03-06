#!/usr/bin/python
#
# Copyright (c) 2013 NORDUnet A/S
# All rights reserved.
#
#   Redistribution and use in source and binary forms, with or
#   without modification, are permitted provided that the following
#   conditions are met:
#
#     1. Redistributions of source code must retain the above copyright
#        notice, this list of conditions and the following disclaimer.
#     2. Redistributions in binary form must reproduce the above
#        copyright notice, this list of conditions and the following
#        disclaimer in the documentation and/or other materials provided
#        with the distribution.
#     3. Neither the name of the NORDUnet nor the names of its
#        contributors may be used to endorse or promote products derived
#        from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# Author : Fredrik Thulin <fredrik@thulin.net>
#

"""
Test OATH authentication.
"""

import os
import sys
import time
import struct
import unittest
import pkg_resources
from pprint import pprint, pformat
import hmac
from hashlib import sha1

import pyhsm
#import pyhsm.oath_hotp

import vccs_auth

from common import FakeCredentialStore

class FakeLogger():

    def audit(self, data):
        sys.stdout.write("AUDIT: {!r}\n".format(data))

class TestOathAuthentication(unittest.TestCase):

    def setUp(self):
        debug = False
        datadir = pkg_resources.resource_filename(__name__, 'data')
        self.config_file = os.path.join(datadir, 'test_config.ini')
        self.config = vccs_auth.config.VCCSAuthConfig(self.config_file, debug)
        self.key_handle = 0x2001
        self.keys = {self.key_handle: str('2001' * 16).decode('hex'),
                     }
        self.hasher = vccs_auth.hasher.VCCSSoftHasher(self.keys, vccs_auth.hasher.NoOpLock())
        self.logger = FakeLogger()
        self.credstore = FakeCredentialStore(creds={})

        self.test_key = '3132333435363738393031323334353637383930'.decode('hex')
        self.nonce = '010203040506'.decode('hex')
        flags = struct.pack('< I', pyhsm.defines.YSM_HMAC_SHA1_GENERATE)
        self.aead = pyhsm.soft_hsm.aesCCM(self.keys[self.key_handle], self.key_handle, self.nonce,
                                          self.test_key + flags, decrypt = False)
        print "Generated AEAD {!r}".format(self.aead.encode('hex'))


    def test_OATH_HOTP_1(self):
        """
        Test OATH HOTP (event based) authentication.
        """
        user_id = 'ft@example.net'
        # Generate an AEAD with the OATH RFC test key
        cred_dict = {'status' : 'active',
                     'nonce' : self.nonce,
                     'version' : 'NDNv1',
                     'credential_id' : '4712',
                     'key_handle' : self.key_handle,
                     'aead' : self.aead,
                     'type' : 'oath-hotp',
                     'digits' : 6,
                     'oath_counter' : 1,
                     'user_id': user_id,
                     }
        cred = vccs_auth.credential.from_dict(cred_dict, {})
        self.credstore.add_credential(cred)

        # this request matches the examples/example-oath-json output
        req = {
            'credential_id': cred.id(),
            'type': 'oath-hotp',
            'user_code': '338314',
            }

        factor = vccs_auth.oath.from_factor(req, 'auth', user_id, self.credstore, self.config)
        self.assertTrue(factor.authenticate(self.hasher, None, self.logger))

        # test with the same code again (should fail, not because of re-use but because
        # the starting offset of the credential has now moved past where the user_code
        # was found)
        self.assertFalse(factor.authenticate(self.hasher, None, self.logger))

        # test with next code (OATH RFC test vector, counter = 5)
        req['user_code'] = '254676'
        factor = vccs_auth.oath.from_factor(req, 'auth', user_id, self.credstore, self.config)
        self.assertTrue(factor.authenticate(self.hasher, None, self.logger))

    def test_OATH_HOTP_bad_user_id(self):
        """
        Test OATH HOTP (event based) authentication with someone else's credential.
        """
        user_id = 'alice@example.net'
        # Generate an AEAD with the OATH RFC test key
        cred_dict = {'status' : 'active',
                     'nonce' : self.nonce,
                     'version' : 'NDNv1',
                     'credential_id' : '4712',
                     'key_handle' : self.key_handle,
                     'aead' : self.aead,
                     'type' : 'oath-hotp',
                     'digits' : 6,
                     'oath_counter' : 1,
                     'user_id': user_id,
                     }
        cred = vccs_auth.credential.from_dict(cred_dict, {})
        self.credstore.add_credential(cred)

        # this request matches the examples/example-oath-json output
        req = {
            'credential_id': cred.id(),
            'type': 'oath-hotp',
            'user_code': '338314',
            }

        factor = vccs_auth.oath.from_factor(req, 'auth', 'bob@example.net', self.credstore, self.config)
        self.assertFalse(factor.authenticate(self.hasher, None, self.logger))


    def test_OATH_TOTP_1(self):
        """
        Test OATH TOTP (time based) authentication.
        """
        user_id = 'ft@example.net'
        # Generate an AEAD with the OATH RFC test key
        cred_dict = {'status': 'active',
                     'nonce': self.nonce,
                     'version': 'NDNv1',
                     'credential_id': '4712',
                     'key_handle': self.key_handle,
                     'aead': self.aead,
                     'type': 'oath-totp',
                     'digits': 8,
                     'oath_counter': (int(time.time()) / 30) - 10,
                     'user_id': user_id,
                     }
        cred = vccs_auth.credential.from_dict(cred_dict, {})
        self.credstore.add_credential(cred)

        correct_code = _get_oath_code(self.test_key, (int(time.time()) / 30), cred.digits())

        req = {
            'credential_id': cred.id(),
            'type': 'oath-totp',
            'user_code': str((correct_code - 11111111) % 10000000),
            }

        factor = vccs_auth.oath.from_factor(req, 'auth', user_id, self.credstore, self.config)
        # Try with incorrect OATH code
        self.assertFalse(factor.authenticate(self.hasher, None, self.logger))

        # Try with correct code
        req['user_code'] = str(correct_code)
        factor = vccs_auth.oath.from_factor(req, 'auth', user_id, self.credstore, self.config)
        self.assertTrue(factor.authenticate(self.hasher, None, self.logger))

        # Try with correct code again, should be refused (replay)
        factor = vccs_auth.oath.from_factor(req, 'auth', user_id, self.credstore, self.config)
        self.assertFalse(factor.authenticate(self.hasher, None, self.logger))

    def test_OATH_TOTP_bad_user_id(self):
        """
        Test OATH TOTP (time based) authentication with someone else's credential.
        """
        user_id = 'alice@example.net'
        # Generate an AEAD with the OATH RFC test key
        cred_dict = {'status': 'active',
                     'nonce': self.nonce,
                     'version': 'NDNv1',
                     'credential_id': '4712',
                     'key_handle': self.key_handle,
                     'aead': self.aead,
                     'type': 'oath-totp',
                     'digits': 8,
                     'oath_counter': (int(time.time()) / 30) - 10,
                     'user_id': user_id,
                     }
        cred = vccs_auth.credential.from_dict(cred_dict, {})
        self.credstore.add_credential(cred)

        correct_code = _get_oath_code(self.test_key, (int(time.time()) / 30), cred.digits())

        req = {
            'credential_id': cred.id(),
            'type': 'oath-totp',
            'user_code': str((correct_code - 11111111) % 10000000),
            }

        factor = vccs_auth.oath.from_factor(req, 'auth', 'bob@example.net', self.credstore, self.config)
        # Try with incorrect OATH code
        self.assertFalse(factor.authenticate(self.hasher, None, self.logger))


def _get_oath_code(hmac_key, counter, digits=6):
    data = struct.pack("> Q", counter)
    hmac_result = hmac.new(hmac_key, msg=data, digestmod=sha1).digest()
    return pyhsm.oath_hotp.truncate(hmac_result, length=digits)
