"""Windows CurrentUser DPAPI; deliberately no insecure cross-platform fallback."""

import ctypes
import os
import re
from pathlib import Path


class Vault:
    def __init__(self, directory):
        self.directory = Path(directory) / "keys"
        self.directory.mkdir(exist_ok=True, parents=True)

    def _path(self, ref):
        if not re.fullmatch(r"[a-f0-9]{32}", ref):
            raise ValueError("密钥引用无效")
        return self.directory / (ref + ".dpapi")

    @staticmethod
    def _crypt(data, decrypt=False):
        if os.name != "nt":
            raise RuntimeError(
                "安全密钥保存仅支持 Windows DPAPI；开发测试请注入内存测试密钥库"
            )
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.DWORD),
                ("data", ctypes.POINTER(ctypes.c_ubyte)),
            ]

        buffer = ctypes.create_string_buffer(data)
        source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(Blob),
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(Blob),
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(Blob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(Blob),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        if decrypt:
            success = crypt32.CryptUnprotectData(
                ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
            )
        else:
            success = crypt32.CryptProtectData(
                ctypes.byref(source),
                "GKM API credential",
                None,
                None,
                None,
                1,
                ctypes.byref(target),
            )
        if not success:
            raise RuntimeError(
                "Windows 无法解密/保存密钥，请确认使用同一 Windows 用户并检查数据目录权限"
            )
        try:
            return ctypes.string_at(target.data, target.length)
        finally:
            kernel32.LocalFree(target.data)

    def save(self, ref, key):
        if not key.strip():
            raise ValueError("密钥不能为空")
        encrypted = self._crypt(key.encode())
        target = self._path(ref)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        os.replace(temporary, target)

    def get(self, ref):
        return self._crypt(self._path(ref).read_bytes(), True).decode()

    def has(self, ref):
        return self._path(ref).exists()
