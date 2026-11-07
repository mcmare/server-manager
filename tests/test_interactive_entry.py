"""Interaction stays the default; positional arguments are optional shortcuts."""
import io
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch
from server_manager.cli import main


class InteractiveEntryTests(unittest.TestCase):
    def test_no_arguments_opens_menu(self):
        with patch('server_manager.cli.StateStore'):
            with patch('builtins.input', side_effect=['8', '0']):
                with patch('server_manager.cli.list_listeners', return_value=[]):
                    with patch('sys.stdout', new_callable=io.StringIO) as out:
                        self.assertEqual(main([]), 0)
        self.assertIn('SERVER MANAGER', out.getvalue())
        self.assertIn('Порты', out.getvalue())

    def test_script_launcher_uses_interactive_default(self):
        script = Path(__file__).resolve().parents[1] / 'server-manager.py'
        self.assertTrue(script.exists())
        with patch('server_manager.cli.main', return_value=0) as main_mock:
            with self.assertRaises(SystemExit) as status:
                runpy.run_path(str(script), run_name='__main__')
        self.assertEqual(status.exception.code, 0)
        main_mock.assert_called_once_with()

    def test_help_explains_interactive_default(self):
        with patch('sys.stdout', new_callable=io.StringIO) as out:
            with self.assertRaises(SystemExit) as status:
                main(['--help'])
        self.assertEqual(status.exception.code, 0)
        self.assertIn('Без аргументов', out.getvalue())

    def test_end_of_input_exits_menu_cleanly(self):
        with patch('server_manager.cli.StateStore'):
            with patch('builtins.input', side_effect=EOFError):
                with patch('sys.stdout', new_callable=io.StringIO):
                    self.assertEqual(main([]), 0)
