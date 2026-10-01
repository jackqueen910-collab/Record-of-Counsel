"""Exercise ROC's owner window with a fake vault and fictional keys only."""
import gc
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from roc.ai_credentials import ENV_NAME
from roc.common import RocError
from roc.owner_setup import main


@unittest.skipUnless(os.name == 'nt', 'Windows owner setup UI')
class OwnerSetupTests(unittest.TestCase):
    def test_save_failure_remove_and_environment_override(self):
        # The production owner window is also a standalone process. Keep its
        # Tcl runtime out of the suite's multithreaded PACER/HTTP worker tests.
        result=subprocess.run([sys.executable,'-c',
            'from tests.test_owner_setup import OwnerSetupTests; OwnerSetupTests()._exercise_window()'],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def _exercise_window(self):
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk(); root.withdraw()
        store = Mock()

        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)

        def exercise():
            children=list(widgets(root))
            entry=next(w for w in children if isinstance(w,ttk.Entry))
            buttons={w.cget('text'):w for w in children if isinstance(w,ttk.Button)}
            labels=lambda:' '.join(w.cget('text') for w in children if isinstance(w,ttk.Label))
            entry.insert(0,'fictional-owner-key')
            buttons['Save ROC account'].invoke()
            store.save.assert_called_once_with('fictional-owner-key')
            self.assertEqual(entry.get(),'')
            self.assertIn('Saved.',labels())
            store.save.side_effect=RocError('Windows could not save the ROC credential.')
            entry.insert(0,'fictional-replacement')
            buttons['Save ROC account'].invoke()
            self.assertEqual(entry.get(),'')
            self.assertIn('could not save',labels())
            os.environ[ENV_NAME]='fictional-backend-key'
            entry.insert(0,'should-not-override')
            buttons['Save ROC account'].invoke()
            self.assertEqual(store.save.call_count,2)
            self.assertIn('already configured',labels())
            buttons['Remove saved key'].invoke()
            store.remove.assert_called_once_with()
            self.assertEqual(entry.get(),'')
            self.assertNotIn('fictional-',labels())

        try:
            with patch.dict(os.environ), patch('tkinter.Tk',return_value=root), \
                 patch.object(root,'mainloop',side_effect=exercise), \
                 patch('roc.owner_setup.WindowsCredentialStore',return_value=store), \
                 patch('tkinter.messagebox.askyesno',return_value=True):
                os.environ.pop(ENV_NAME,None)
                self.assertEqual(main(),0)
        finally:
            root.destroy()
            # Finalize Tk variables on the GUI thread before later worker tests.
            gc.collect()


if __name__ == '__main__':
    unittest.main()
