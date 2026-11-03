"""MEGA AES-CTR decryption and per-chunk integrity verification.

Protocol reference: siphon/internal/megacrypto (MIT); see docs/sources.md.
"""
import base64
import hmac
import json
import struct
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def decode(value):
    value=value.strip().replace(',','').rstrip('=')
    return base64.urlsafe_b64decode(value+'='*(-len(value)%4))


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def xor(left,right):
    return bytes(a^b for a,b in zip(left,right))


def file_key(packed):
    if len(packed)!=32:raise ValueError('MEGA file links must include the complete decryption key.')
    return xor(packed[:16],packed[16:]),packed[16:24],packed[24:]


def decrypt(key,data,mode):
    cipher=Cipher(algorithms.AES(key),mode).decryptor()
    return cipher.update(data)+cipher.finalize()


def attributes(key,value):
    raw=decrypt(key,decode(value),modes.CBC(bytes(16))).rstrip(b'\0')
    if not raw.startswith(b'MEGA'):raise ValueError('The MEGA decryption key is invalid.')
    return json.loads(raw[4:])


class Decoder:
    def __init__(self,packed):
        self.key,self.nonce,self.expected=file_key(packed)
        self.cipher=Cipher(algorithms.AES(self.key),modes.CTR(self.nonce+bytes(8))).decryptor()
        self.fold=bytes(16)
        self.buffer=bytearray()
        self.index=1

    def update(self,encrypted):
        plain=self.cipher.update(encrypted)
        self.buffer.extend(plain)
        while len(self.buffer)>=min(self.index,8)*128*1024:
            length=min(self.index,8)*128*1024
            self.chunk(bytes(self.buffer[:length]))
            del self.buffer[:length]
            self.index+=1
        return plain

    def chunk(self,data):
        data+=bytes(-len(data)%16)
        cipher=Cipher(algorithms.AES(self.key),modes.CBC(self.nonce*2)).encryptor()
        mac=(cipher.update(data)+cipher.finalize())[-16:]
        fold=Cipher(algorithms.AES(self.key),modes.ECB()).encryptor()
        self.fold=fold.update(xor(self.fold,mac))+fold.finalize()

    def finish(self):
        self.cipher.finalize()
        if self.buffer:self.chunk(bytes(self.buffer))
        a,b,c,d=struct.unpack('>4I',self.fold)
        if not hmac.compare_digest(struct.pack('>2I',a^b,c^d),self.expected):
            raise ValueError('MEGA integrity check failed. Retry this file; it was not uploaded.')
