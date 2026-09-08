import copy
import importlib.util
import json
import tempfile
import io
import hashlib
from email.message import Message
from unittest.mock import patch
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('website_export', Path(__file__).parents[1] / 'scripts/export_website_manifest.py')
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)

class WebsiteExportTests(unittest.TestCase):
    def setUp(self):
        self.asset = {'asset_id':'bam-example', 'workflow_state':'APPROVED', 'media_kind':'IMAGE', 'original_sha256':'a'*64, 'alt':'Reviewed aerial view of an example roof', 'service_tags':['inspections'], 'source_path':'/private/do-not-export', 'latitude':40.7}
        self.variant = {'parent_asset_id':'bam-example','format':'webp','width':800,'height':600,'byte_size':1234,'sha256':'b'*64,'r2_storage_key':'bam/web/stills/bam-example/800.webp'}
    def build(self):
        return export.compile_manifest([self.asset],[self.variant],'https://media.example.invalid')
    def test_schema_uses_actual_object_key_and_omits_private_fields(self):
        m=self.build();a=m['assets']['bam-example'];self.assertEqual(a['src'],'https://media.example.invalid/bam/web/stills/bam-example/800.webp');self.assertEqual(m['publication']['status'],'staged')
        self.assertNotIn('/private',json.dumps(m));self.assertNotIn('latitude',json.dumps(m));self.assertNotIn('East Orange',json.dumps(m))
    def test_unapproved_rejected(self):
        self.asset['workflow_state']='AI_APPROVED'
        with self.assertRaisesRegex(ValueError,'human approval'):self.build()
    def test_missing_variants_rejected(self):
        with self.assertRaisesRegex(ValueError,'WebP derivative'):export.compile_manifest([self.asset],[],'https://media.example.invalid')
    def test_wrong_tags_rejected(self):
        self.asset['service_tags']=['thermal-diagnosis']
        with self.assertRaisesRegex(ValueError,'service tags'):self.build()
    def test_duplicate_content_rejected(self):
        other=copy.deepcopy(self.asset);other['asset_id']='second'
        with self.assertRaisesRegex(ValueError,'duplicate original'):export.compile_manifest([self.asset,other],[self.variant],'https://media.example.invalid')
    def test_paths_cannot_escape_origin(self):
        for key in ['../private.webp','/root.webp','https://other.invalid/a.webp','folder/../a.webp']:
            self.variant['r2_storage_key']=key
            with self.assertRaises(ValueError):self.build()
    def test_same_width_derivatives_are_deduplicated(self):
        other={**self.variant,'r2_storage_key':'bam/web/stills/bam-example/800b.webp'}
        m=export.compile_manifest([self.asset],[self.variant,other],'https://media.example.invalid');self.assertEqual(len(m['assets']['bam-example']['variants']),1)
    def test_atomic_export(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'manifest.json';path.write_text('old');export.atomic_write(path,self.build());self.assertEqual(json.loads(path.read_text())['version'],1);self.assertEqual(len(list(Path(folder).iterdir())),1)

    def test_remote_verification_checks_actual_bytes_and_cors(self):
        content=b'synthetic webp bytes'
        self.variant.update(byte_size=len(content),sha256=hashlib.sha256(content).hexdigest())
        manifest=self.build()
        class Response(io.BytesIO):
            status=200
            headers=Message()
        Response.headers['Content-Type']='image/webp'
        Response.headers['Access-Control-Allow-Origin']='https://baronaerial.com'
        with patch.object(export,'build_opener') as opener:
            opener.return_value.open.return_value=Response(content)
            export.verify_remote(manifest)
        self.assertEqual(manifest['publication']['status'],'verified')
        self.assertIn('verifiedAt',manifest['publication'])
    def test_remote_mismatch_keeps_manifest_staged(self):
        class Response(io.BytesIO):
            status=200
            headers=Message()
        Response.headers['Content-Type']='image/webp'
        Response.headers['Access-Control-Allow-Origin']='*'
        manifest=self.build()
        with patch.object(export,'build_opener') as opener:
            opener.return_value.open.return_value=Response(b'wrong')
            with self.assertRaisesRegex(ValueError,'byte count'):export.verify_remote(manifest)
        self.assertEqual(manifest['publication']['status'],'staged')

if __name__=='__main__':unittest.main()
