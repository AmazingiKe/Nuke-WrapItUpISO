import tempfile, unittest, json
from unittest.mock import patch
from pathlib import Path
from core import scan, package, Cancelled

class PackagingTests(unittest.TestCase):
    def test_scan_reports_node_progress(self):
        updates = []
        scan(self.script, progress=lambda *values: updates.append(values))
        self.assertEqual(updates[0][0], 0)
        self.assertGreater(updates[-1][1], 0)
        self.assertEqual(updates[-1][0], updates[-1][1])
        self.assertEqual([u[0] for u in updates], sorted(u[0] for u in updates))

    def test_copy_reports_partial_bytes_and_can_cancel_mid_file(self):
        source = self.root / 'large.exr'
        source.write_bytes(b'x' * (9 * 1024 * 1024))
        self.script.write_text('Read {\n file large.exr\n name large\n}\n', encoding='utf-8')
        plan = scan(self.script)
        updates = []
        package(plan, self.root / 'complete', byte_progress=lambda *values: updates.append(values))
        self.assertTrue(any(0 < done < total for done, total, name in updates))
        self.assertEqual(updates[-1][:2], (source.stat().st_size, source.stat().st_size))
        updates.clear()
        with self.assertRaises(Cancelled):
            package(plan, self.root / 'cancelled',
                    cancel=lambda: any(done > 0 for done, total, name in updates),
                    byte_progress=lambda *values: updates.append(values))
        self.assertEqual(list((self.root / 'cancelled').iterdir()), [])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        (self.root/'plates').mkdir(); (self.root/'other').mkdir()
        for frame in (1,3): (self.root/'plates'/f'image.{frame:04}.exr').write_bytes(bytes([frame])*20)
        (self.root/'other'/'image.0001.exr').write_bytes(b'different')
        self.script=self.root/'中文 shot.nk'
        self.script.write_text('''version 15.0 v1
Root {
 name old/location.nk
 project_directory "'''+self.root.as_posix()+'''"
}
Group {
 name nested
}
Read {
 file plates/image.%04d.exr
 first 1
 last 3
 name sequence
 label "fake { Read { file NOT_A_FILE } }"
}
Read {
 file plates/image.####.exr
 first 1
 last 3
 name duplicate
}
Read {
 file other/image.0001.exr
 name same_name
}
end_group
Read {
 file "[value root.foo]/dynamic.exr"
 name dynamic
}
Read {
 file missing.exr
 name missing
}
Write {
 file render/output.%04d.exr
 name output
}
''',encoding='utf-8')
    def tearDown(self): self.tmp.cleanup()
    def test_scan_and_collect(self):
        plan=scan(self.script); self.assertEqual(len(plan.assets),5)
        self.assertEqual(plan.assets[0].missing,[2]); self.assertEqual(plan.assets[3].status,'待处理')
        target=package(plan,self.root/'packed')
        collected=list((target/'media').rglob('*.exr')); self.assertEqual(len(collected),3)
        self.assertEqual(sorted(p.read_bytes() for p in collected),sorted([bytes([1])*20,bytes([3])*20,b'different']))
        text=(target/self.script.name).read_text(encoding='utf-8-sig')
        self.assertIn('file render/output.%04d.exr',text); self.assertIn('fake { Read { file NOT_A_FILE } }',text)
        self.assertIn('[file dirname [value root.name]]/media/',text)
        self.assertIn('file missing.exr',text); self.assertIn('[value root.foo]/dynamic.exr',text)
        report=json.loads((target/'report.json').read_text(encoding='utf-8')); self.assertEqual(report['copied_files'],3)
        original=self.script.read_bytes()
        with self.assertRaises(FileExistsError): package(plan,self.root/'packed')
        self.assertEqual(original,self.script.read_bytes())
    def test_cancel_atomic(self):
        plan=scan(self.script); state={'stop':False}
        def tick(*args): state['stop']=True
        with self.assertRaises(Cancelled): package(plan,self.root/'packed',lambda:state['stop'],tick)
        self.assertEqual(list((self.root/'packed').iterdir()),[])
    def test_braces_spaces_udim_and_environment(self):
        folder=self.root/'space folder'; folder.mkdir(); (folder/'tile.1001.exr').write_bytes(b'udim')
        self.script.write_text('Read {\n file {space folder/tile.<UDIM>.exr}\n name tile\n}\nRead {\n file {$NUKE_PACKAGER_UNDEFINED/file.exr}\n name env\n}\n')
        plan=scan(self.script); self.assertEqual(len(plan.assets[0].files),1); self.assertEqual(plan.assets[1].status,'待处理')
    def test_malformed(self):
        self.script.write_text('Read {\n file hello.exr\n')
        with self.assertRaises(ValueError): scan(self.script)
    def test_unresolved_project_does_not_guess(self):
        self.script.write_text('Root {\n project_directory "[getenv PROJECT]"\n}\nRead {\n file plates/image.%04d.exr\n}\n')
        plan=scan(self.script); self.assertEqual(plan.assets[0].status,'待处理'); self.assertEqual(plan.assets[0].files,[])
    def test_windows_rename_denied_no_longer_used(self):
        plan=scan(self.script)
        with patch.object(Path,'rename',side_effect=PermissionError('[WinError 5] Access is denied')):
            target=package(plan,self.root/'packed')
        self.assertTrue((target/'report.json').is_file())
        self.assertFalse((target/'PACKAGING_INCOMPLETE.txt').exists())
    def test_command_blocks_and_builtin_warning_noise(self):
        names='Retime Colorspace Input Remove Keyer Expression ShuffleCopy Output Cryptomatte Multiply Keymix HueCorrect Copy Denoise2 Defocus AddChannels Invert Grain2 ColorLookup NoTimeBlur AdjBBox CopyBBox STMap TimeBlur'.split()
        self.script.write_text('define_window_layout_xml {\n <layout>fake</layout>\n}\nadd_layer {rgba red green blue alpha}\n' + ''.join(f'{cls} {{\n name {cls}1\n}}\n' for cls in names) + 'MyCustom {\n name one\n}\nMyCustom {\n name two\n}\n')
        plan=scan(self.script)
        self.assertEqual(len(plan.warnings),2)
        self.assertIn('MyCustom × 2',plan.warnings[0])
        self.assertNotIn('define_window_layout_xml',' '.join(plan.warnings))
        self.assertEqual(plan.assets,[])
    def test_read_only_source_cancel_and_write_permissions(self):
        import stat
        source=self.root/'other/image.0001.exr'; source.chmod(stat.S_IREAD)
        try:
            plan=scan(self.script); target=package(plan,self.root/'packed')
            copied=next(p for p in (target/'media').rglob('image.0001.exr') if p.read_bytes()==b'different')
            self.assertTrue(copied.stat().st_mode & stat.S_IWUSR)
            with self.assertRaises(Cancelled): package(plan,self.root/'cancelled',lambda:True)
            self.assertEqual(list((self.root/'cancelled').iterdir()),[])
        finally: source.chmod(stat.S_IWRITE|stat.S_IREAD)

if __name__=='__main__': unittest.main()
