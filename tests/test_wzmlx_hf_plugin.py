"""Offline contract tests for the WZML-X v3 Hugging Face mirror plugin.

These are stubs, not a real Hugging Face upload or live Telegram end-to-end test.
"""
import ast
import asyncio
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugins" / "hf_uploader" / "hf_uploader.py"
MANIFEST = ROOT / "plugins" / "hf_uploader" / "wzml_plugin.yml"


def stub_module(name, **attributes):
    mod = types.ModuleType(name)
    mod.__dict__.update(attributes)
    mod.__path__ = []  # allow package-style imports
    sys.modules[name] = mod
    return mod


class FakeButtons:
    def __init__(self):
        self.items = []

    def data_button(self, label, data, **kwargs):
        self.items.append(("callback", label, data))

    def url_button(self, label, url, **kwargs):
        self.items.append(("url", label, url))

    def build_menu(self, columns=1):
        return list(self.items)


class FakeStatus:
    STATUS_DOWNLOAD = "Download"
    STATUS_UPLOAD = "Upload"
    STATUS_QUEUEDL = "QueueDl"


class FakeChat:
    id = 200
    type = "private"


class FakeUser:
    id = 101


class FakeMessage:
    id = 501
    from_user = FakeUser()
    sender_chat = None
    chat = FakeChat()
    date = datetime.now(timezone.utc)
    text = "/hfmirror https://example.com/demo.mp4"
    reply_to_message = None


class PluginContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()
        cls.tree = ast.parse(cls.source)
        cls.manifest = MANIFEST.read_text()

    def test_syntax_and_v3_protocol(self):
        classes = {
            node.name: node for node in self.tree.body
            if isinstance(node, ast.ClassDef)
        }
        self.assertIn("HFNativeStatus", classes)
        methods = {
            node.name for node in classes["HFNativeStatus"].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in ("name", "gid", "status", "progress", "processed_bytes",
                     "size", "speed", "eta", "task", "cancel_task"):
            self.assertIn(name, methods)
        self.assertIn("task_dict[message.id] = native_status", self.source)
        self.assertIn("await send_status_message(message)", self.source)
        self.assertIn("await delete_status()", self.source)

    def test_manifest_callback_and_version(self):
        self.assertIn('version: "1.1.0"', self.manifest)
        self.assertIn('pattern: "^hfu:"', self.manifest)
        self.assertIn("handler: hf_action_callback", self.manifest)
        self.assertIn("access: owner", self.manifest)

    def test_button_callbacks_and_native_cancel(self):
        self.assertIn("Refresh Status", self.source)
        self.assertIn("Cancel Transfer", self.source)
        self.assertIn("current.cancel()", self.source)

    def test_running_task_receives_native_buttons_without_hfstatus(self):
        async def exercise():
            recorded = {"status": 0, "sent": []}
            modules = {}
            module_names = [
                "aiohttp", "pyrogram", "pyrogram.enums",
                "bot", "bot.core", "bot.core.plugin_manager",
                "bot.core.tg_client", "bot.helper",
                "bot.helper.ext_utils", "bot.helper.ext_utils.status_utils",
                "bot.helper.telegram_helper",
                "bot.helper.telegram_helper.button_build",
                "bot.helper.telegram_helper.message_utils",
            ]
            for name in module_names:
                modules[name] = sys.modules.get(name)
            try:
                stub_module("aiohttp", ClientSession=object, ClientTimeout=object)
                stub_module("pyrogram")
                stub_module("pyrogram.enums",
                    ButtonStyle=types.SimpleNamespace(PRIMARY="primary",DANGER="danger"),
                    ChatType=types.SimpleNamespace(
                        PRIVATE="private",SUPERGROUP="supergroup",
                        CHANNEL="channel", FORUM="forum"
                    ),
                )
                task_dict = {}
                lock = asyncio.Lock()
                logger = types.SimpleNamespace(
                    warning=lambda *args, **kwargs: None,
                    error=lambda *args, **kwargs: None
                )
                stub_module("bot", DOWNLOAD_DIR="/tmp/", LOGGER=logger,
                            task_dict=task_dict, task_dict_lock=lock)
                stub_module("bot.core")
                stub_module("bot.core.plugin_manager",
                            PluginBase=object,
                            get_plugin_manager=lambda: types.SimpleNamespace(
                                effective_config=lambda _name: {
                                    "repo_id": "test/repo",
                                    "repo_type": "dataset",
                                    "max_jobs": 2
                                }
                            ))
                stub_module("bot.core.tg_client",
                            TgClient=types.SimpleNamespace(bot=None))
                stub_module("bot.helper")
                stub_module("bot.helper.ext_utils")
                stub_module("bot.helper.ext_utils.status_utils",
                            MirrorStatus=FakeStatus,
                            get_readable_file_size=lambda n: str(int(n))+"B",
                            get_readable_time=lambda n: str(int(n))+"s")
                stub_module("bot.helper.telegram_helper")
                stub_module("bot.helper.telegram_helper.button_build",
                            ButtonMaker=FakeButtons)
                async def send_message(message, text, buttons=None, **_):
                    recorded["sent"].append((text,buttons))
                async def send_status_message(message):
                    recorded["status"] += 1
                async def noop(*args, **kwargs):
                    return None
                stub_module("bot.helper.telegram_helper.message_utils",
                            delete_status=noop,send_message=send_message,
                            send_status_message=send_status_message,
                            update_status_message=noop)

                spec = importlib.util.spec_from_file_location("plugin_contract_fixture", SOURCE)
                plugin = importlib.util.module_from_spec(spec)
                with patch.dict(os.environ, {"HF_TOKEN": "hf_fake_token"}):
                    spec.loader.exec_module(plugin)
                    async def fake_run(*args, **kwargs):
                        await asyncio.sleep(10)
                    plugin._run = fake_run
                    message = FakeMessage()
                    await plugin.hfmirror_command(None, message)
                    self.assertEqual(recorded["status"], 1)
                    self.assertIn(message.id, task_dict)
                    native = task_dict[message.id]
                    self.assertEqual(native.status(), FakeStatus.STATUS_QUEUEDL)
                    self.assertEqual(native.listener.user_id, message.from_user.id)
                    self.assertEqual(native.listener.mode[1], "#HuggingFace")
                    self.assertTrue(recorded["sent"])
                    buttons = recorded["sent"][-1][1]
                    self.assertEqual(len(buttons), 2)
                    self.assertIn("hfu:refresh:", buttons[0][2])
                    self.assertIn("hfu:cancel:", buttons[1][2])
                    await native.cancel_task()
                    await asyncio.gather(plugin.JOBS[native.gid()]["task"],
                                         return_exceptions=True)
            finally:
                for name, old in modules.items():
                    if old is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = old
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
