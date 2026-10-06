import unittest
from applications.securequote_lite.auth import password_hash,verify_password
class T(unittest.TestCase):
 def test_password(self):
  x=password_hash("a-very-long-password"); self.assertTrue(verify_password("a-very-long-password",x)); self.assertFalse(verify_password("wrong",x))
 def test_salt(self):self.assertNotEqual(password_hash("a-very-long-password"),password_hash("a-very-long-password"))
