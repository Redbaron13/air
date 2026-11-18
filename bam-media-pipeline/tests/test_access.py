import asyncio
import base64
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch
s=importlib.util.spec_from_file_location('access',Path(__file__).parents[1]/'server/access.py')
a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
class AccessTests(unittest.TestCase):
 def check_gate(self,headers=(),kind='http',secret='x'*32):
  events=[]
  async def downstream(scope,receive,send): await send({'allowed':True})
  async def send(event): events.append(event)
  with patch.dict(os.environ,{'PIPELINE_OPERATOR_TOKEN':secret}):
   asyncio.run(a.OperatorGate(downstream)({'type':kind,'headers':headers},None,send))
  return events
 def test_no_credential_denied(self): self.assertEqual(self.check_gate()[0]['status'],401)
 def test_missing_configuration_fails_closed(self): self.assertEqual(self.check_gate(secret='')[0]['status'],503)
 def test_bearer_allowed(self): self.assertTrue(self.check_gate([(b'authorization',b'Bearer '+b'x'*32)])[0]['allowed'])
 def test_basic_allowed(self):
  v=base64.b64encode(b'operator:'+b'x'*32)
  self.assertTrue(self.check_gate([(b'authorization',b'Basic '+v)])[0]['allowed'])
 def test_cross_origin_denied(self):
  self.assertEqual(self.check_gate([(b'authorization',b'Bearer '+b'x'*32),(b'origin',b'https://untrusted.invalid')])[0]['status'],401)
 def test_websocket_denied(self): self.assertEqual(self.check_gate(kind='websocket')[0]['code'],1008)
 def test_malformed_basic_denied(self): self.assertEqual(self.check_gate([(b'authorization',b'Basic !@#')])[0]['status'],401)
