"""Authenticated encryption for PTB's single-file state; key stays on the server."""
from io import BytesIO
import os
from pathlib import Path
import pickle
import tempfile

from cryptography.fernet import Fernet
from telegram.ext import PicklePersistence
from telegram.ext._picklepersistence import _BotPickler, _BotUnpickler

MAGIC = b'HUMOENC1\n'


class SecurePersistence(PicklePersistence):
    def __init__(self, filepath):
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path = path.with_suffix('.key')
        if path.is_symlink() or key_path.is_symlink():
            raise RuntimeError('State/key symlinks are not permitted')
        if not key_path.exists():
            if path.exists() and path.read_bytes().startswith(MAGIC):
                raise RuntimeError('Encrypted state key is missing; restore the key backup')
            try:
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
        key_path.chmod(0o600)
        self._cipher = Fernet(key_path.read_bytes().strip())
        super().__init__(filepath=path)

    def _load_singlefile(self):
        if not self.filepath.exists():
            self.conversations, self.user_data, self.chat_data = {}, {}, {}
            self.bot_data = self.context_types.bot_data()
            self.callback_data = None
            return
        raw = self.filepath.read_bytes()
        legacy = not raw.startswith(MAGIC)
        if legacy:
            # Only migrate trusted local state; pickle is never safe for untrusted input.
            stat = self.filepath.stat()
            if stat.st_uid != os.getuid() or stat.st_mode & 0o022:
                raise RuntimeError('Legacy state must be owned by this user and not writable by others')
        else:
            raw = self._cipher.decrypt(raw[len(MAGIC):])
        data = _BotUnpickler(self.bot, BytesIO(raw)).load()
        self.user_data, self.chat_data = data['user_data'], data['chat_data']
        self.bot_data = data.get('bot_data', self.context_types.bot_data())
        self.callback_data = data.get('callback_data')
        self.conversations = data['conversations']
        if legacy:
            self._dump_singlefile()
        else:
            self.filepath.chmod(0o600)

    def _dump_singlefile(self):
        data = dict(conversations=self.conversations, user_data=self.user_data,
                    chat_data=self.chat_data, bot_data=self.bot_data,
                    callback_data=self.callback_data)
        buffer = BytesIO()
        _BotPickler(self.bot, buffer, protocol=pickle.HIGHEST_PROTOCOL).dump(data)
        encrypted = MAGIC + self._cipher.encrypt(buffer.getvalue())
        fd, name = tempfile.mkstemp(prefix='.state-', dir=self.filepath.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.filepath)
        finally:
            if os.path.exists(name):
                os.unlink(name)
