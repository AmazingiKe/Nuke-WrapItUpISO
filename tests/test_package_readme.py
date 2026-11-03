import unittest
from package_readme import render_package_readme

class PackageReadmeTests(unittest.TestCase):
    def test_complete_partial_and_missing_are_listed_without_reasons(self):
        report = {'source_script':'D:/shot.nk','assets':[
            {'node':'Read1','original':'D:/ok.exr','status':'就绪','files':1,'packed_path':'media/ok.exr'},
            {'node':'Read2','original':'D:/seq.%04d.exr','status':'缺帧','files':2,'packed_path':'media/seq.%04d.exr','note':'SECRET_REASON'},
            {'node':'Read3','original':'D:/missing.exr','status':'缺失','files':0,'note':'SECRET_REASON'},
        ],'plugins':[{'name':'Demo','status':'已收集','packed_path':'gizmos/Demo.gizmo'},
                     {'name':'Missing','status':'未收集','packed_path':None,'note':'SECRET_REASON'}]}
        text=render_package_readme(report)
        for expected in ('Read1 | ok.exr | 成功','Read2 | seq.%04d.exr | 部分成功','Read3 | missing.exr | 未成功',
                         'Demo | 成功','Missing | 未成功','打开工程.cmd','report.json'):
            self.assertIn(expected,text)
        self.assertNotIn('SECRET_REASON',text)
        self.assertNotIn('缺帧',text)

    def test_ready_without_copied_files_is_not_reported_successful(self):
        report={'source_script':'shot.nk','assets':[{'node':'empty','original':'empty.exr','status':'就绪','files':0}], 'plugins':[]}
        text=render_package_readme(report)
        self.assertIn('empty | empty.exr | 未成功',text)
        self.assertNotIn('打开工程.cmd',text)
        self.assertIn('shot.nk',text)

    def test_no_dependencies_and_markdown_escaping(self):
        text=render_package_readme({'source_script':'shot.nk','assets':[], 'plugins':[]})
        self.assertIn('无素材',text)
        report={'source_script':'shot.nk','assets':[{'node':'Read|1\nnext','original':'<shot>.exr','files':0}], 'plugins':[]}
        text=render_package_readme(report)
        self.assertIn(r'Read\|1 next',text)
        self.assertIn('&lt;shot&gt;.exr',text)

if __name__=='__main__': unittest.main()
