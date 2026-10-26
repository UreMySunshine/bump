import asyncio
import json
import os
import runpy
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

M = runpy.run_path(os.environ.get('BUMP_PATH', str(Path(__file__).with_name('bump'))), run_name='bump_test')
G = M['check_brew'].__globals__


class Detectors(unittest.TestCase):
    def setUp(self):
        override = patch.dict(G, UV_STANDALONE_PATH='/example/bin/uv')
        override.start()
        self.addCleanup(override.stop)

    def collect(self, fn, results):
        events = []
        with patch.dict(G, command_json=lambda *a, **k: next(results)):
            M[fn](events.append)
        return [p for k, p in events if k == 'tool']

    def test_brew_complete_formula_cask_and_dependency(self):
        inventory = {'formulae': [
            {'name': 'fresh', 'installed': [{'version': '1.0', 'installed_on_request': True}]},
            {'name': 'dep', 'installed': [{'version': '1.0'}, {'version': '0.9'}]},
            {'name': 'old', 'installed': [{'version': '1.0'}]},
            {'name': 'pinned', 'installed': [{'version': '1.0'}], 'pinned': True}],
            'casks': [{'token': 'app', 'name': ['App'], 'installed': '1.0'}]}
        old = {'formulae': [{'name': 'old', 'current_version': '2.0'}, {'name': 'pinned', 'current_version': '2.0'}],
               'casks': [{'name': 'app', 'current_version': '2.0'}]}
        tools = self.collect('check_brew', iter([inventory, old]))
        self.assertEqual(len(tools), 5)
        self.assertEqual([t.name for t in tools if t.outdated], ['old', 'app'])
        self.assertTrue(tools[1].dependency)
        self.assertEqual(tools[1].local, '1.0, 0.9')

    def test_npm_complete(self):
        tools = self.collect('check_npm', iter([
            {'dependencies': {'fresh': {'version': '1.0'}, '@scope/old': {'version': '1.0'}}},
            {'@scope/old': {'current': '1.0', 'latest': '2.0'}}]))
        self.assertEqual(len(tools), 2)
        self.assertFalse(tools[0].outdated)
        self.assertTrue(tools[1].outdated)

    def test_failed_update_check_preserves_inventory_as_unknown(self):
        for fn, inventory in [('check_npm', {'dependencies': {'demo': {'version': '1.0'}}}),
                              ('check_brew', {'formulae': [{'name': 'demo', 'installed': [{'version': '1.0'}]}]})]:
            calls = iter([inventory, RuntimeError('网络失败')])
            def result(*args, **kwargs):
                value = next(calls)
                if isinstance(value, Exception):
                    raise value
                return value
            events = []
            with patch.dict(G, command_json=result):
                M[fn](events.append)
            tool = next(p for k, p in events if k == 'tool')
            self.assertEqual(tool.state, 'unknown')
            self.assertFalse(tool.outdated)
            self.assertEqual(tool.local, '1.0')
            self.assertEqual(sum(k == 'group_done' for k, _ in events), 1)

    def test_command_failures_and_npm_code_one(self):
        from subprocess import CompletedProcess
        for code, stdout, allowed, fails in [(2, '{}', (0, 1), True), (0, '{"error":{"summary":"失败"}}', (0,), True),
                                            (1, '{}', (0, 1), False), (1, '', (0, 1), True)]:
            with patch.dict(G, run=lambda *a, **k: CompletedProcess([], code, stdout, '')):
                if fails:
                    with self.assertRaises(RuntimeError):
                        M['command_json'](['npm'], allowed)
                else:
                    self.assertEqual(M['command_json'](['npm'], allowed), {})

    def test_uv_verified_installation_route(self):
        from tempfile import TemporaryDirectory
        import subprocess
        with patch.object(G['shutil'], 'which', return_value=G['UV_STANDALONE_PATH']),              patch.object(G['os'].path, 'islink', return_value=False):
            command, description = M['uv_update_command']()
        self.assertEqual(command[-1], '/example/bin')
        self.assertIn('官方安装脚本', description)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            curl = root / 'curl'
            result = root / 'result'
            curl.write_text("#!/bin/sh\nfor last; do :; done\nprintf '%s\n' 'printf \"%s|%s\" \"$UV_INSTALL_DIR\" \"$UV_NO_MODIFY_PATH\" > \"$TEST_RESULT\"' > \"$last\"\n")
            curl.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ':/usr/bin:/bin', TEST_RESULT=str(result))
            completed = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(result.read_text(), '/example/bin|1')
            result.unlink()
            curl.write_text('#!/bin/sh\nexit 22\n')
            completed = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 22)
            self.assertFalse(result.exists())
        for executable, linked in [('/opt/homebrew/bin/uv', True),
                                   ('/other/bin/uv', False),
                                   (G['UV_STANDALONE_PATH'], True)]:
            with patch.object(G['shutil'], 'which', return_value=executable),                  patch.object(G['os'].path, 'islink', return_value=linked):
                self.assertEqual(M['uv_update_command']()[0], ['uv', 'self', 'update'])

    def test_version_baseline(self):
        self.assertTrue(M['is_newer']('1.29.2', 'rustup 1.29.1'))
        self.assertFalse(M['is_newer']('1.0', '1.0.0'))
        self.assertTrue(M['is_newer']('2026-10-20', '2026-09-25'))

    def test_update_output_streams_before_exit_and_preserves_failure(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            gate = Path(directory) / 'continue'
            tool = M['Tool']('demo', 'npm', 'demo')
            tool.update_cmd = [sys.executable, '-u', '-c',
                'import pathlib,sys,time; print("Downloading demo\\r", flush=True); '
                'gate=pathlib.Path(sys.argv[1]); '
                '\nwhile not gate.exists(): time.sleep(0.01)\n'
                'print("Installing demo", flush=True); print("permission denied", flush=True); sys.exit(1)', str(gate)]
            downloading = threading.Event()
            lines, result = [], []
            def output(line):
                lines.append(line)
                downloading.set()
            worker = threading.Thread(target=lambda: result.append(M['run_update'](tool, output)))
            worker.start()
            try:
                self.assertTrue(downloading.wait(5))
                self.assertTrue(worker.is_alive())
                self.assertEqual(M['update_stage'](lines[0]), '正在下载')
            finally:
                gate.touch()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(M['update_stage'](lines[1]), '正在安装')
            self.assertIsNone(M['update_stage']('checking version'))
            self.assertFalse(result[0][0])
            self.assertIn('permission denied', result[0][1])

    def test_update_timeout_keeps_output(self):
        tool = M['Tool']('demo', 'npm', 'demo')
        tool.update_cmd = [sys.executable, '-u', '-c',
                           'import time; print("Downloading demo", flush=True); time.sleep(5)']
        with patch.dict(G, UPDATE_TIMEOUT=0.3):
            ok, message = M['run_update'](tool)
        self.assertFalse(ok)
        self.assertIn('更新超时', message)
        self.assertIn('Downloading demo', message)


class Interface(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tempfile import TemporaryDirectory
        self.config = TemporaryDirectory()
        self.addCleanup(self.config.cleanup)
        override = patch.dict(os.environ, XDG_CONFIG_HOME=self.config.name)
        override.start()
        self.addCleanup(override.stop)

    async def test_invalid_theme_preferences(self):
        path = M['preferences_path']()
        path.parent.mkdir(parents=True)
        for contents in ['broken json', '[]', '{"theme":"unsupported"}']:
            path.write_text(contents)
            self.assertEqual(M['load_theme_mode'](), 'dark')
        M['save_theme_mode']('terminal')
        self.assertEqual(M['load_theme_mode'](), 'terminal')

    async def test_enter_opens_then_confirms_update(self):
        tool = M['Tool']('demo', 'npm', 'demo')
        tool.local, tool.latest = '1', '2'
        def collect(publish):
            publish(('tool', tool))
            for group in M['GROUPS']:
                publish(('group_done', group))
        with patch.dict(G, collect=collect):
            app = M['BumpApp']()
            with patch.object(app, 'begin_update') as update:
                async with app.run_test(size=(140, 40)) as pilot:
                    await pilot.pause()
                    await pilot.press('space', 'enter')
                    self.assertIsInstance(app.screen, M['ConfirmUpdate'])
                    update.assert_not_called()
                    await pilot.press('enter')
                    await pilot.pause()
                    update.assert_called_once_with([tool])
                    app.action_request_update()
                    await pilot.pause()
                    await pilot.press('escape')
                    self.assertEqual(update.call_count, 1)

    async def test_update_progress_success_and_failure(self):
        import threading
        gates = [threading.Event(), threading.Event()]
        started = [threading.Event(), threading.Event()]
        installing = threading.Event()
        finish_install = threading.Event()
        targets = []
        for i in range(2):
            tool = M['Tool'](str(i), 'npm', 'demo-' + str(i))
            tool.local, tool.latest = '1', '2'
            targets.append(tool)
        def collect(publish):
            for tool in targets:
                publish(('tool', tool))
            for group in M['GROUPS']:
                publish(('group_done', group))
        def update(tool, on_output):
            i = targets.index(tool)
            on_output("Downloading demo-" + str(i))
            started[i].set()
            gates[i].wait(5)
            if not i:
                on_output('Installing demo-0')
                installing.set()
                finish_install.wait(5)
            if i:
                raise RuntimeError('模拟失败')
            return True, ''
        with patch.dict(G, collect=collect, run_update=update):
            app = M['BumpApp']()
            async with app.run_test(size=(140, 40)) as pilot:
                try:
                    await pilot.pause()
                    app.begin_update(targets)
                    await pilot.pause()
                    progress = app.query_one('#update-items')
                    self.assertTrue(started[0].is_set())
                    self.assertEqual(str(progress.get_cell('0', 'stage')), '正在下载')
                    self.assertEqual(str(progress.get_cell('1', 'stage')), '等待更新')
                    self.assertTrue(app.query_one('#update-progress-box').display)
                    gates[0].set()
                    for _ in range(30):
                        await pilot.pause(0.05)
                        if installing.is_set() and str(progress.get_cell('0', 'stage')) == '正在安装':
                            break
                    self.assertEqual(str(progress.get_cell('0', 'stage')), '正在安装')
                    self.assertEqual(str(progress.get_cell('1', 'stage')), '等待更新')
                    await pilot.resize_terminal(90, 28)
                    await pilot.pause()
                    self.assertGreater(progress.size.height, 2)
                    self.assertLessEqual(progress.region.bottom, 28)
                    finish_install.set()
                    for _ in range(30):
                        await pilot.pause(0.05)
                        if started[1].is_set() and str(progress.get_cell('0', 'stage')) == '更新成功':
                            break
                    self.assertEqual(str(progress.get_cell('0', 'stage')), '更新成功')
                    self.assertEqual(str(progress.get_cell('1', 'stage')), '正在下载')
                    app.action_theme_toggle()
                    self.assertEqual(str(progress.get_cell('0', 'stage')), '更新成功')
                    gates[1].set()
                    for _ in range(30):
                        await pilot.pause(0.05)
                        if not app.updating:
                            break
                    self.assertFalse(app.updating)
                    self.assertIsInstance(app.screen, M['UpdateResult'])
                    self.assertIn('模拟失败', app.last_update_result)
                    await pilot.press('escape')
                    app.query_one('#result-button').press()
                    await pilot.pause()
                    self.assertIsInstance(app.screen, M['UpdateResult'])
                    await pilot.press('escape')
                    self.assertEqual(str(progress.get_cell('1', 'stage')), '更新失败')
                    self.assertIn('模拟失败', str(progress.get_cell('1', 'detail')))
                    self.assertTrue(app.query_one('#table-box').display)
                    self.assertIn('成功 1 · 失败 1', str(app.query_one('#update-summary').content))
                finally:
                    finish_install.set()
                    for gate in gates:
                        gate.set()

    async def test_filter_select_clear_and_cancel_update(self):
        tools = []
        for i in range(49):
            t = M['Tool'](str(i), 'brew' if i < 32 else 'npm' if i < 42 else 'self', 'package-%02d' % i)
            t.local, t.latest = '1.0', '2.0' if i >= 46 else '1.0'
            if i == 0:
                t.local = "1.0, 0.9"
            tools.append(t)
        def collect(publish):
            for t in tools:
                publish(('tool', t))
            for group in M['GROUPS']:
                publish(('group_done', group))
        with patch.dict(G, collect=collect, run_update=lambda *a: self.fail('不应执行升级')):
            app = M['BumpApp']()
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                self.assertEqual(len(app.filtered_tools), 49)
                self.assertEqual(str(app.query_one(M["DataTable"]).get_row("0")[3]), "1.0, 0.9")
                self.assertFalse(app.selected)
                self.assertEqual(app.query_one(M['DataTable']).cursor_row, 0)
                await pilot.press('j')
                self.assertEqual(app.query_one(M['DataTable']).cursor_row, 1)
                await pilot.press('k')
                self.assertEqual(app.query_one(M['DataTable']).cursor_row, 0)
                await pilot.resize_terminal(90, 40)
                await pilot.pause()
                await pilot.press('l')
                await pilot.pause()
                self.assertGreater(app.query_one(M['DataTable']).scroll_x, 0)
                await pilot.press('h')
                await pilot.pause()
                self.assertEqual(app.query_one(M['DataTable']).scroll_x, 0)
                await pilot.resize_terminal(140, 40)
                await pilot.press('enter')
                self.assertNotIsInstance(app.screen, M['ConfirmUpdate'])
                await pilot.press('space')
                self.assertEqual(len(app.selected), 1)
                dark_bg = app.screen.styles.background
                await pilot.press('ctrl+t')
                await pilot.pause()
                self.assertEqual(app.theme, 'bump-light')
                self.assertEqual(M['BumpApp']().theme, 'bump-light')
                self.assertNotEqual(app.screen.styles.background, dark_bg)
                self.assertEqual(len(app.selected), 1)
                await pilot.press('enter')
                await pilot.pause()
                self.assertIsInstance(app.screen, M['ConfirmUpdate'])
                await pilot.press('escape')
                await pilot.pause()
                self.assertFalse(app.updating)
                app.query_one('#theme-toggle').press()
                await pilot.pause()
                self.assertEqual(app.theme, 'bump-terminal')
                self.assertTrue(app.native_ansi_color)
                self.assertEqual(M['load_theme_mode'](), 'terminal')
                self.assertEqual(M['BumpApp']().theme, 'bump-terminal')
                app.action_theme_toggle()
                await pilot.pause()
                self.assertEqual(app.theme, 'bump-dark')
                self.assertEqual(app.screen.styles.background, dark_bg)
                app.action_clear()
                app.query_one('#source').value = 'npm'
                await pilot.pause()
                self.assertEqual(len(app.filtered_tools), 10)
                app.query_one('#search').value = 'package-33'
                await pilot.pause()
                self.assertEqual([t.name for t in app.filtered_tools], ['package-33'])
                app.query_one('#search').focus()
                app.query_one('#search').value = ''
                await pilot.press('h', 'j', 'k', 'l')
                self.assertEqual(app.query_one('#search').value, 'hjkl')
                app.query_one('#search').value = 'no-match'
                await pilot.pause()
                self.assertEqual(len(app.filtered_tools), 0)
                app.action_refresh()
                await pilot.pause(0.2)
                self.assertEqual(len(app.tools), 49)
                await pilot.resize_terminal(90, 28)
                await pilot.pause()
                self.assertFalse(app.selected)


if __name__ == '__main__':
    unittest.main()
