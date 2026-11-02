import tempfile
import unittest
from pathlib import Path
from telegram.ext import ExtBot as Bot
from cryptography.fernet import InvalidToken
from bot.secure_persistence import SecurePersistence, MAGIC


class SecurePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_encrypted_roundtrip_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.pkl'
            first = SecurePersistence(path)
            first.set_bot(Bot('123:TEST'))
            await first.get_user_data()
            await first.update_user_data(7, {'phone': 'private-phone'})
            await first.flush()
            self.assertTrue(path.read_bytes().startswith(MAGIC))
            self.assertNotIn(b'private-phone', path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            second = SecurePersistence(path)
            second.set_bot(Bot('123:TEST'))
            self.assertEqual((await second.get_user_data())[7]['phone'], 'private-phone')
            raw = bytearray(path.read_bytes())
            raw[-5] ^= 1
            path.write_bytes(raw)
            third = SecurePersistence(path)
            third.set_bot(Bot('123:TEST'))
            with self.assertRaises(InvalidToken):
                await third.get_user_data()

    async def test_legacy_migration_and_missing_key_fail_closed(self):
        from telegram.ext import PicklePersistence
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.pkl'
            legacy = PicklePersistence(path)
            legacy.set_bot(Bot('123:TEST'))
            await legacy.get_user_data()
            await legacy.update_user_data(8, {'name': 'private-name'})
            await legacy.flush()
            secure = SecurePersistence(path)
            secure.set_bot(Bot('123:TEST'))
            self.assertEqual((await secure.get_user_data())[8]['name'], 'private-name')
            self.assertTrue(path.read_bytes().startswith(MAGIC))
            path.with_suffix('.key').unlink()
            with self.assertRaises(RuntimeError):
                SecurePersistence(path)
