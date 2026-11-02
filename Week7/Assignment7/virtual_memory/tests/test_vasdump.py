#!/usr/bin/env python3
"""Functional tests for parsing, accounting partitions, and child lifetime."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vasdump',ROOT/'level1/vasdump.py')
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)

class VasdumpTests(unittest.TestCase):
    def test_parser_and_priority(self):
        text = '1000-3000 r--p 00000000 08:01 42 /tmp/a path.so (deleted)\n'
        text += ''.join(f'{name}: {i+1} kB\n' for i,name in enumerate(v.FIELDS))
        with tempfile.NamedTemporaryFile('w') as stream:
            stream.write(text);stream.flush()
            records=v.parse_smaps(stream.name)
        self.assertEqual(records[0]['path'],'/tmp/a path.so (deleted)')
        self.assertEqual(records[0]['fields']['Anonymous'],8)
        self.assertEqual(v.classify(records[0],'/tmp/a path.so'),'executable')
        self.assertEqual(v.classify(records[0],'/tmp/other'),'shared-library')
        for name,category in [('[heap]','heap'),('[stack]','stack'),('[anon:label]','special'),('','anonymous'),('/tmp/file','other-file')]:
            item=records[0]|{'path':name}
            self.assertEqual(v.classify(item,'/tmp/other'),category)

    def test_missing_field_is_incomplete(self):
        with tempfile.NamedTemporaryFile('w') as stream:
            stream.write('1000-2000 rw-p 00000000 00:00 0\nSize: 4 kB\n');stream.flush()
            with self.assertRaisesRegex(ValueError,'incomplete'):
                v.parse_smaps(stream.name)

    def test_partition_totals_and_pid_lifetime(self):
        p=subprocess.Popen(['sleep','10'])
        try:
            time.sleep(.03)
            records=v.snapshot(p.pid)
            for field in v.FIELDS:
                overall=sum(item['fields'][field] for item in records)
                for key in ('category','permissions'):
                    self.assertEqual(sum(row[field] for row in v.totals(records,key).values()),overall)
            result=subprocess.run([sys.executable,str(ROOT/'level1/vasdump.py'),str(p.pid)],capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIsNone(p.poll())
        finally:
            p.terminate();p.wait()

    def test_command_failure_and_early_exit(self):
        for args in [['--','/this-command-does-not-exist'],['--delay','.05','--','true'],['--delay','nan','--','sleep','1'],['0']]:
            result=subprocess.run([sys.executable,str(ROOT/'level1/vasdump.py'),*args],capture_output=True,timeout=3)
            self.assertNotEqual(result.returncode,0)

    def test_sigkill_fallback_reaps(self):
        start=time.monotonic()
        result=subprocess.run([sys.executable,str(ROOT/'level1/vasdump.py'),'--delay','.15','--',sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(10)'],capture_output=True,timeout=4)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertLess(time.monotonic()-start,3)

    def test_diff_sort_added_removed(self):
        def item(start,size,rss):
            fields=dict.fromkeys(v.FIELDS,0) | dict(Size=size,Rss=rss)
            return dict(start=start,end=start+4096,path='',fields=fields,category='anonymous')
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream):
            v.diff_report([item(4096,4,1),item(8192,4,4)],[item(4096,4,2),item(12288,4,3)])
        rows=stream.getvalue().splitlines()
        self.assertTrue(rows[1].startswith('removed 2000-3000'))
        self.assertTrue(rows[2].startswith('added 3000-4000'))
        self.assertTrue(rows[3].startswith('changed 1000-2000'))

if __name__=='__main__': unittest.main()
